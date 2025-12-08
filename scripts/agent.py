#!/usr/bin/env python3
"""
Autonomous Research Agent
=========================

An agentic research assistant that autonomously:
1. Plans research by decomposing topics into questions
2. Searches for academic papers (Semantic Scholar + paper-scraper + Exa.ai)
3. Acquires papers by downloading PDFs to the library
4. Synthesizes information using PaperQA2 RAG with persistent Qdrant
5. Generates a Typst document with proper citations

Uses Gemini 3 Pro Preview for planning and writing with function calling
to orchestrate the research pipeline. Outputs compiled Typst documents
in the reports/ folder using the project's typst-template.

Usage:
    research agent "Impact of attention mechanisms on NLP"
    
Output:
    reports/<timestamp>_<topic>/
    ├── lib.typ      # Template (from templates/typst-template/)
    ├── refs.bib     # Citations (filtered to cited papers only)
    ├── main.typ     # Generated document
    └── main.pdf     # Compiled PDF
"""

import sys
import os
import json
import subprocess
import shutil
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Set

# Suppress verbose logging
os.environ['LITELLM_LOG'] = 'ERROR'

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from google import genai
from google.genai import types

# Setup
console = Console()
load_dotenv()

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "library"
REPORTS_PATH = REPO_ROOT / "reports"
TEMPLATE_PATH = REPO_ROOT / "templates" / "typst-template"
MASTER_BIB = REPO_ROOT / "master.bib"
PAPIS_CONFIG = REPO_ROOT / "papis.config"

# Configure Gemini client
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

# Models - using Gemini 3 Pro Preview as specified
AGENT_MODEL = "gemini-3-pro-preview"
FLASH_MODEL = "gemini-2.5-flash"  # For RAG (faster)

# Global state for tracking which papers were used
_used_citation_keys: Set[str] = set()


# ============================================================================
# TOOL FUNCTIONS - Wrapping existing CLI infrastructure
# ============================================================================

def discover_papers(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    """
    Search for academic papers using BOTH Semantic Scholar AND paper-scraper.
    
    This is the unified discovery tool that combines multiple sources:
    - Semantic Scholar (~200M papers, citation counts)
    - Paper-scraper (PubMed, bioRxiv, Springer, arXiv)
    
    Use this tool FIRST to discover relevant papers before adding them.
    
    Args:
        query: Search query string describing the research topic
               (e.g., "transformer attention mechanism", "vision transformers")
        limit: Maximum number of results to return (default: 15)
    
    Returns:
        List of paper metadata with: title, authors, year, abstract, arxiv_id, doi, citations, source
    """
    from semanticscholar import SemanticScholar
    import itertools
    
    console.print(f"[dim]🔍 Unified search: {query}[/dim]")
    
    papers = []
    seen_ids = set()
    
    # 1. Search Semantic Scholar
    console.print("[dim]  → Semantic Scholar...[/dim]")
    sch = SemanticScholar()
    try:
        results = sch.search_paper(query, limit=limit)
        for paper in itertools.islice(results, limit):
            arxiv_id = None
            doi = None
            if paper.externalIds:
                arxiv_id = paper.externalIds.get('ArXiv')
                doi = paper.externalIds.get('DOI')
            
            # Dedup key
            key = doi or arxiv_id or paper.title[:50]
            if key in seen_ids:
                continue
            seen_ids.add(key)
            
            papers.append({
                'title': paper.title,
                'authors': [a.name for a in paper.authors][:3],
                'year': paper.year,
                'abstract': paper.abstract[:400] if paper.abstract else None,
                'arxiv_id': arxiv_id,
                'doi': doi,
                'citations': paper.citationCount or 0,
                'source': 'S2'
            })
    except Exception as e:
        console.print(f"[yellow]S2 error: {e}[/yellow]")
    
    # 2. Search paper-scraper (with timeout to prevent blocking)
    console.print("[dim]  → paper-scraper...[/dim]")
    try:
        import concurrent.futures
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from utils import scraper_client
        
        # Run with timeout to prevent blocking on network issues
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(scraper_client.search_papers, query, 10)
            try:
                ps_results = future.result(timeout=15)  # 15 second timeout
            except concurrent.futures.TimeoutError:
                console.print("[dim]paper-scraper timed out, continuing with S2 results[/dim]")
                ps_results = []
        
        for paper in ps_results:
            arxiv_id = paper.get('arxiv_id')
            doi = paper.get('doi')
            
            key = doi or arxiv_id or paper.get('title', '')[:50]
            if key in seen_ids:
                continue
            seen_ids.add(key)
            
            papers.append({
                'title': paper.get('title', 'Unknown'),
                'authors': paper.get('authors', [])[:3],
                'year': paper.get('year'),
                'abstract': paper.get('abstract', '')[:400] if paper.get('abstract') else None,
                'arxiv_id': arxiv_id,
                'doi': doi,
                'citations': 0,
                'source': 'PS'
            })
    except Exception as e:
        console.print(f"[dim]paper-scraper: {e}[/dim]")
    
    console.print(f"[green]✓ Found {len(papers)} unique papers[/green]")
    return papers[:limit]


def exa_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Neural/semantic search using Exa.ai for concept-based discovery.
    
    ⚠️ COSTS CREDITS - Use sparingly! Only when:
    - discover_papers doesn't find relevant results
    - You need conceptual/semantic matching beyond keywords
    - Looking for recent or obscure papers
    
    Args:
        query: Natural language query (can be more conceptual)
        limit: Max results (default: 5 to conserve credits)
    
    Returns:
        List of paper metadata
    """
    console.print(f"[dim]🧠 Exa.ai search (costs credits): {query}[/dim]")
    
    try:
        from exa_py import Exa
        exa_key = os.getenv('EXA_API_KEY')
        if not exa_key:
            return [{"error": "EXA_API_KEY not configured"}]
        
        exa = Exa(api_key=exa_key)
        results = exa.search_and_contents(
            query,
            type="neural",
            use_autoprompt=True,
            num_results=limit,
            text={"max_characters": 500}
        )
        
        papers = []
        for r in results.results:
            # Try to extract arxiv ID from URL
            arxiv_id = None
            if 'arxiv.org' in r.url:
                match = re.search(r'(\d{4}\.\d{4,5})', r.url)
                if match:
                    arxiv_id = match.group(1)
            
            papers.append({
                'title': r.title,
                'url': r.url,
                'abstract': r.text[:400] if r.text else None,
                'arxiv_id': arxiv_id,
                'source': 'Exa'
            })
        
        console.print(f"[green]✓ Exa found {len(papers)} results[/green]")
        return papers
    except Exception as e:
        console.print(f"[red]Exa error: {e}[/red]")
        return []


def add_paper(identifier: str, source: str = "auto") -> Dict[str, Any]:
    """
    Add a paper to the local library by its identifier.
    
    Downloads the PDF via papis and updates master.bib automatically.
    Use this after finding relevant papers via discover_papers.
    
    Args:
        identifier: Paper identifier - either:
                   - arXiv ID (e.g., "1706.03762" for "Attention Is All You Need")
                   - DOI (e.g., "10.1038/nature12373")
        source: Source type - "arxiv", "doi", or "auto" (auto-detect from format)
    
    Returns:
        Dict with 'status' ("success" or "error") and 'citation_key' if successful
    """
    # Auto-detect source
    if source == "auto":
        if identifier.startswith("10."):
            source = "doi"
        else:
            source = "arxiv"
    
    console.print(f"[dim]📥 Adding: {source}:{identifier}[/dim]")
    
    venv_bin = os.path.dirname(sys.executable)
    papis_cmd = os.path.join(venv_bin, "papis")
    
    cmd = [papis_cmd, "--config", str(PAPIS_CONFIG), "-l", "main", "add", "--batch", "--from", source, identifier]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            # Sync master.bib using existing utility
            try:
                sys.path.insert(0, str(REPO_ROOT / "scripts"))
                from utils.sync_bib import sync_master_bib
                sync_master_bib()
            except Exception as e:
                console.print(f"[yellow]Warning: bib sync issue: {e}[/yellow]")
            
            console.print(f"[green]✓ Added {identifier}[/green]")
            return {"status": "success", "identifier": identifier}
        else:
            return {"status": "error", "message": result.stderr.strip()[:200]}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Timeout while adding paper"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


def query_library(question: str, paper_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Ask a research question about papers in the library using RAG (PaperQA2).
    
    This uses the PERSISTENT Qdrant vector database for instant queries.
    Returns detailed answers with citations from the indexed papers.
    Use AFTER adding papers to the library.
    
    Args:
        question: Specific research question to answer
                  (e.g., "How does self-attention compute query-key-value?")
        paper_filter: Optional keyword to filter papers (e.g., author name, topic)
    
    Returns:
        Dict with 'answer' text and list of 'sources' used
    """
    from paperqa import Docs, Settings
    from paperqa.llms import QdrantVectorStore
    from qdrant_client import AsyncQdrantClient
    import asyncio
    import hashlib
    
    console.print(f"[dim]🤔 Querying: {question[:60]}...[/dim]")
    
    # Configure settings for Gemini
    settings = Settings()
    settings.llm = f"gemini/{FLASH_MODEL}"
    settings.summary_llm = f"gemini/{FLASH_MODEL}"
    settings.embedding = "gemini/text-embedding-004"
    settings.answer.answer_max_sources = 5
    settings.answer.evidence_k = 10
    
    # Find PDFs
    all_pdfs = list(LIBRARY_PATH.rglob("*.pdf"))
    
    if paper_filter:
        pdfs = [p for p in all_pdfs if paper_filter.lower() in str(p).lower()]
        console.print(f"[dim]Filtered to {len(pdfs)} papers matching '{paper_filter}'[/dim]")
    else:
        pdfs = all_pdfs
    
    if not pdfs:
        return {"answer": "No papers found in library. Use add_paper first.", "sources": []}
    
    # Check for persistent vector store
    db_path = LIBRARY_PATH / ".qa_vectordb"
    fp_path = db_path / ".fingerprint"
    
    # Generate fingerprint from PDF paths only (not mtime for stability)
    fingerprint_data = sorted([str(p) for p in pdfs])
    current_fp = hashlib.md5("\n".join(fingerprint_data).encode()).hexdigest()
    
    docs = None
    
    # Try to load existing store (only for non-filtered queries)
    if not paper_filter and fp_path.exists():
        try:
            stored_fp = fp_path.read_text().strip()
            if stored_fp == current_fp:
                # Load from Qdrant
                client_qdrant = AsyncQdrantClient(path=str(db_path))
                
                async def load_docs():
                    return await QdrantVectorStore.load_docs(
                        client=client_qdrant,
                        collection_name="research_papers"
                    )
                
                try:
                    loop = asyncio.get_event_loop()
                    if not loop.is_running():
                        docs = loop.run_until_complete(load_docs())
                except RuntimeError:
                    docs = asyncio.run(load_docs())
                
                if docs and docs.docnames:
                    console.print(f"[green]✓ Using cached index ({len(docs.docnames)} docs)[/green]")
        except Exception as e:
            console.print(f"[dim]Cache miss: {e}[/dim]")
    
    # Build index if needed
    if not docs:
        if paper_filter:
            docs = Docs()
        else:
            # Create persistent Qdrant store
            db_path.mkdir(exist_ok=True)
            client_qdrant = AsyncQdrantClient(path=str(db_path))
            vector_store = QdrantVectorStore(
                client=client_qdrant,
                collection_name="research_papers"
            )
            docs = Docs(texts_index=vector_store)
        
        console.print(f"[dim]Indexing {len(pdfs)} papers...[/dim]")
        for pdf in pdfs[:25]:  # Reasonable limit
            try:
                docs.add(pdf, settings=settings)
            except:
                pass
        
        # Save fingerprint
        if not paper_filter:
            fp_path.parent.mkdir(exist_ok=True)
            fp_path.write_text(current_fp)
            console.print(f"[green]✓ Index saved for future queries[/green]")
    
    # Query
    try:
        response = docs.query(question, settings=settings)
        sources = []
        if hasattr(response, 'contexts') and response.contexts:
            for ctx in response.contexts[:5]:
                if hasattr(ctx.text, 'name'):
                    sources.append(ctx.text.name)
        
        console.print(f"[green]✓ Got answer ({len(sources)} sources)[/green]")
        return {
            "answer": response.formatted_answer or response.answer,
            "sources": sources
        }
    except Exception as e:
        return {"answer": f"Query error: {e}", "sources": []}


def fuzzy_cite(query: str) -> List[Dict[str, str]]:
    """
    Fuzzy search for citation keys in the library.
    
    Uses fuzzy matching to find papers even with partial or misspelled queries.
    Returns citation keys to use as @citation_key in the Typst document.
    
    Args:
        query: Search term - author name, title fragment, year, keyword
               (e.g., "vaswani", "attention", "2017", "transformer")
    
    Returns:
        List of matching papers with: citation_key, title, authors, year
        Track these keys - they will be included in refs.bib
    """
    global _used_citation_keys
    import yaml
    
    console.print(f"[dim]📚 Fuzzy cite search: {query}[/dim]")
    
    results = []
    query_lower = query.lower()
    query_parts = query_lower.split()
    
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            
            # Build searchable text
            searchable = f"{data.get('ref', '')} {data.get('title', '')} {data.get('author', '')} {data.get('year', '')}".lower()
            
            # Fuzzy match: all query parts must appear somewhere
            if all(part in searchable for part in query_parts):
                citation_key = data.get('ref', 'unknown')
                results.append({
                    "citation_key": citation_key,
                    "title": data.get('title', 'Unknown')[:70],
                    "authors": data.get('author', 'Unknown')[:40],
                    "year": str(data.get('year', ''))
                })
                # Track for refs.bib filtering
                _used_citation_keys.add(citation_key)
        except:
            pass
    
    console.print(f"[green]✓ Found {len(results)} matches[/green]")
    return results[:10]


def list_library() -> List[Dict[str, str]]:
    """
    List all papers currently in the library.
    
    Use this to see what papers are already available for querying
    before deciding to add more.
    
    Returns:
        List of papers with: citation_key, title, authors, year
    """
    import yaml
    
    console.print(f"[dim]📖 Listing library...[/dim]")
    
    papers = []
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            
            papers.append({
                "citation_key": data.get('ref', 'unknown'),
                "title": data.get('title', 'Unknown')[:70],
                "authors": data.get('author', 'Unknown')[:40],
                "year": str(data.get('year', ''))
            })
        except:
            pass
    
    console.print(f"[green]✓ Library has {len(papers)} papers[/green]")
    return papers


# ============================================================================
# TOOL DECLARATIONS FOR GEMINI FUNCTION CALLING
# ============================================================================

TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="discover_papers",
            description="Search for papers using Semantic Scholar + paper-scraper. Use FIRST to find papers.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search query for the topic"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Max results (default 15)")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="exa_search",
            description="Neural search via Exa.ai. COSTS CREDITS - use only when discover_papers isn't enough.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Conceptual search query"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Max results (default 5)")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="add_paper",
            description="Add a paper to library by arXiv ID or DOI. Downloads PDF and updates master.bib.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "identifier": types.Schema(type=types.Type.STRING, description="arXiv ID or DOI"),
                    "source": types.Schema(type=types.Type.STRING, description="'arxiv', 'doi', or 'auto'")
                },
                required=["identifier"]
            )
        ),
        types.FunctionDeclaration(
            name="query_library",
            description="Ask a research question using PaperQA2 RAG. Uses persistent Qdrant index.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "question": types.Schema(type=types.Type.STRING, description="Research question"),
                    "paper_filter": types.Schema(type=types.Type.STRING, description="Optional keyword filter")
                },
                required=["question"]
            )
        ),
        types.FunctionDeclaration(
            name="fuzzy_cite",
            description="Fuzzy search for @citation_keys. Use before writing to get correct keys.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Author, title, year, or keyword")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="list_library",
            description="List all papers in the library. Check existing papers before adding more.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
                required=[]
            )
        )
    ])
]

TOOL_FUNCTIONS = {
    "discover_papers": discover_papers,
    "exa_search": exa_search,
    "add_paper": add_paper,
    "query_library": query_library,
    "fuzzy_cite": fuzzy_cite,
    "list_library": list_library
}

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are an autonomous research agent that produces academic Typst documents.

## Available Tools

### 1. discover_papers(query, limit)
Search Semantic Scholar + paper-scraper for papers.
- Use FIRST to find relevant papers
- Returns: title, authors, year, abstract, arxiv_id, doi, citations
- Prioritize high-citation and recent papers

### 2. exa_search(query, limit)  
Neural search via Exa.ai. ⚠️ COSTS CREDITS!
- Only use when discover_papers doesn't find enough
- Good for conceptual/semantic queries

### 3. add_paper(identifier, source)
Download paper to library by arXiv ID or DOI.
- Updates master.bib automatically
- Add 5-10 MOST RELEVANT papers only

### 4. query_library(question, paper_filter)
RAG Q&A using PaperQA2 with persistent Qdrant index.
- Returns detailed answers with citations
- Use AFTER adding papers

### 5. fuzzy_cite(query)
Find @citation_keys for papers in library.
- Fuzzy matches author, title, year
- Returns keys to use in document

### 6. list_library()
Show all papers in the library.  
- Check before adding duplicates

## Workflow

1. **Explore**: list_library() to see existing papers
2. **Search**: discover_papers() with 2-3 queries
3. **Acquire**: add_paper() for 5-8 best papers
4. **Research**: query_library() with specific questions
5. **Cite**: fuzzy_cite() to get correct @keys
6. **Write**: Output complete Typst document

## Output Format

```typst
#import "lib.typ": project

#show: project.with(
  title: "Your Title",
  subtitle: "A Research Report",
  authors: ("Research Agent",),
  date: "December 2025",
  abstract: [
    Concise abstract summarizing topic and findings.
  ]
)

#outline(indent: auto)
#pagebreak()

= Introduction
Content with @citation_key references...

= Background
More content with citations @key1 @key2...

= Analysis
Detailed synthesis...

= Conclusion
Summary and future directions.

#bibliography("refs.bib")
```

## Rules
- Use @citation_key format for ALL citations
- Use fuzzy_cite() to verify exact keys before writing
- Be thorough and scholarly
- Synthesize information across sources
- Aim for 1500-2500 words"""

# ============================================================================
# AGENT LOOP
# ============================================================================

def run_agent(topic: str) -> str:
    """Run the research agent on a topic."""
    global _used_citation_keys
    _used_citation_keys = set()  # Reset for new run
    
    console.print(Panel(
        f"[bold cyan]🤖 Research Agent[/bold cyan]\n\n"
        f"[white]{topic}[/white]\n\n"
        f"[dim]Model: {AGENT_MODEL}[/dim]",
        border_style="cyan"
    ))
    
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"""Research this topic and produce a Typst document:

TOPIC: {topic}

1. Start by checking existing library papers
2. Search for and add relevant papers
3. Query the library to synthesize information
4. Use fuzzy_cite to get correct citation keys
5. Output a complete Typst document with proper @citations""")]
        )
    ]
    
    console.print("\n[bold]Starting autonomous research...[/bold]\n")
    
    max_iterations = 35
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        with console.status(f"[cyan]Thinking (step {iteration}/{max_iterations})..."):
            try:
                response = client.models.generate_content(
                    model=AGENT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=TOOLS
                    )
                )
            except Exception as e:
                console.print(f"[red]API error: {e}[/red]")
                break
        
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]
            
            if function_calls:
                contents.append(types.Content(role="model", parts=parts))
                
                function_response_parts = []
                for part in function_calls:
                    fc = part.function_call
                    func_name = fc.name
                    func_args = dict(fc.args) if fc.args else {}
                    
                    console.print(f"[yellow]→ {func_name}({json.dumps(func_args, default=str)[:80]})[/yellow]")
                    
                    if func_name in TOOL_FUNCTIONS:
                        try:
                            result = TOOL_FUNCTIONS[func_name](**func_args)
                        except Exception as e:
                            result = {"error": str(e)}
                    else:
                        result = {"error": f"Unknown function: {func_name}"}
                    
                    function_response_parts.append(
                        types.Part(function_response=types.FunctionResponse(
                            name=func_name,
                            response={"result": json.dumps(result, default=str)}
                        ))
                    )
                
                contents.append(types.Content(role="user", parts=function_response_parts))
            else:
                text = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
                
                if "#import" in text and "project.with" in text and "#bibliography" in text:
                    console.print("[green]✓ Document generated[/green]")
                    if "```typst" in text:
                        match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                        if match:
                            return match.group(1).strip()
                    return text
                
                console.print(f"[dim]{text[:150]}...[/dim]")
                
                contents.append(types.Content(role="model", parts=parts))
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text="Continue. Output the complete Typst document when ready.")]
                ))
        else:
            console.print("[yellow]Empty response, retrying...[/yellow]")
    
    return "// Agent did not produce a document within iteration limit"


def filter_bibtex_to_cited(master_bib_path: Path, cited_keys: Set[str]) -> str:
    """Filter master.bib to only include entries that were cited."""
    if not master_bib_path.exists():
        return "% No references\n"
    
    content = master_bib_path.read_text()
    
    # Parse bibtex entries
    pattern = r'(@\w+\{([^,]+),.*?\n\})'
    matches = re.findall(pattern, content, re.DOTALL)
    
    filtered_entries = []
    for full_entry, key in matches:
        key = key.strip()
        if key in cited_keys or not cited_keys:
            filtered_entries.append(full_entry)
    
    if not filtered_entries:
        # Fallback: return all entries if no matches
        return content
    
    return "\n\n".join(filtered_entries) + "\n"


def extract_citations_from_typst(typst_content: str) -> Set[str]:
    """Extract all @citation_key references from Typst content."""
    # Match @key patterns (not in code blocks)
    pattern = r'@([a-zA-Z][a-zA-Z0-9_-]*)'
    matches = re.findall(pattern, typst_content)
    return set(matches)


def generate_report(topic: str) -> Path:
    """Generate a complete research report on a topic."""
    global _used_citation_keys
    
    REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    
    # Run agent
    typst_content = run_agent(topic)
    
    # Create report directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    topic_slug = "".join(c if c.isalnum() or c == " " else "" for c in topic[:40])
    topic_slug = topic_slug.strip().replace(" ", "_").lower()
    report_name = f"{timestamp}_{topic_slug}"
    report_dir = REPORTS_PATH / report_name
    report_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy template library
    if (TEMPLATE_PATH / "lib.typ").exists():
        shutil.copy(TEMPLATE_PATH / "lib.typ", report_dir / "lib.typ")
        console.print(f"[dim]✓ Copied lib.typ[/dim]")
    
    # Extract cited keys from document
    doc_citations = extract_citations_from_typst(typst_content)
    all_cited = _used_citation_keys | doc_citations
    
    console.print(f"[dim]Cited keys: {all_cited}[/dim]")
    
    # Create filtered refs.bib with only cited papers
    if MASTER_BIB.exists():
        filtered_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
        (report_dir / "refs.bib").write_text(filtered_bib)
        console.print(f"[green]✓ Created refs.bib (filtered to cited papers)[/green]")
    else:
        (report_dir / "refs.bib").write_text("% No references\n")
    
    # Write main.typ
    main_typ = report_dir / "main.typ"
    main_typ.write_text(typst_content)
    console.print(f"[green]✓ Created main.typ[/green]")
    
    # Compile to PDF
    console.print("[dim]Compiling to PDF...[/dim]")
    try:
        result = subprocess.run(
            ["typst", "compile", "main.typ"],
            cwd=report_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            console.print(f"[green]✓ Compiled main.pdf[/green]")
        else:
            console.print(f"[yellow]Compilation: {result.stderr[:150]}[/yellow]")
    except FileNotFoundError:
        console.print("[yellow]typst not found - install: brew install typst[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Compile error: {e}[/yellow]")
    
    console.print(Panel(
        f"[bold green]📄 Report Generated[/bold green]\n\n"
        f"📁 {report_dir}\n"
        f"📝 main.typ\n"
        f"📚 refs.bib (filtered)\n"
        f"📖 main.pdf",
        border_style="green"
    ))
    
    return report_dir


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Autonomous Research Agent - Produces academic Typst documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  research agent "Impact of attention mechanisms on NLP"
  research agent "Vision Transformers vs CNNs"
        """
    )
    parser.add_argument('topic', nargs='+', help='Research topic')
    
    args = parser.parse_args()
    topic = " ".join(args.topic)
    
    if not topic:
        parser.print_help()
        sys.exit(1)
    
    if not os.getenv('GEMINI_API_KEY'):
        console.print("[red]Error: GEMINI_API_KEY not set[/red]")
        sys.exit(1)
    
    generate_report(topic)
