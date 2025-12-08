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
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from google import genai
from google.genai import types
import logging

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

# Debug logger (set per session)
_debug_logger: Optional[logging.Logger] = None


def setup_debug_log(report_dir: Path) -> logging.Logger:
    """Setup debug logging to a file in the report directory."""
    log_file = report_dir / "artifacts" / "debug.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger(f"agent_{report_dir.name}")
    logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers
    logger.handlers = []
    
    # File handler for debug log
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logger.addHandler(fh)
    
    return logger


def log_debug(msg: str):
    """Log debug message to file only."""
    global _debug_logger
    if _debug_logger:
        _debug_logger.debug(msg)


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


def validate_citations(citation_keys: List[str]) -> Dict[str, Any]:
    """
    Validate that citation keys exist in the library.
    
    Use this BEFORE writing the final document to ensure all @keys are valid.
    Invalid keys will cause compilation errors.
    
    Args:
        citation_keys: List of citation keys to validate (without @ symbol)
                      e.g., ["vaswani2017attention", "devlin2018bert"]
    
    Returns:
        Dict with 'valid' keys, 'invalid' keys, and 'suggestions' for invalid ones
    """
    import yaml
    
    console.print(f"[dim]🔍 Validating {len(citation_keys)} citations...[/dim]")
    
    # Build library of all valid keys
    library_keys = {}
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            key = data.get('ref', '')
            if key:
                library_keys[key.lower()] = key
        except:
            pass
    
    valid = []
    invalid = []
    suggestions = {}
    
    for key in citation_keys:
        key_lower = key.lower()
        if key_lower in library_keys:
            valid.append(library_keys[key_lower])
        else:
            invalid.append(key)
            # Find similar keys
            for lib_key in library_keys.values():
                if any(part in lib_key.lower() for part in key_lower.split('_')):
                    suggestions[key] = lib_key
                    break
    
    if invalid:
        console.print(f"[yellow]⚠ {len(invalid)} invalid keys: {invalid[:5]}[/yellow]")
    else:
        console.print(f"[green]✓ All {len(valid)} citations valid[/green]")
    
    return {
        "valid": valid,
        "invalid": invalid,
        "suggestions": suggestions,
        "all_library_keys": list(library_keys.values())[:20]
    }


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
            description="Fuzzy search for @citation_keys. Returns keys that ACTUALLY exist in the library.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Author, title, year, or keyword")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="validate_citations",
            description="Validate citation keys before writing. Use to ensure all @keys exist in library.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "citation_keys": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description="List of citation keys to validate"
                    )
                },
                required=["citation_keys"]
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
    "validate_citations": validate_citations,
    "list_library": list_library
}

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are an autonomous research agent that produces academic Typst documents.

You are a RIGOROUS SCHOLAR. Every claim must be grounded. Every citation must be verified.

## Available Tools

### 1. query_library(question, paper_filter)
RAG Q&A using PaperQA2 with persistent Qdrant index.
- START HERE to see what knowledge exists in the library
- Returns detailed answers with citations from indexed papers
- Use first to understand current knowledge, then identify gaps

### 2. list_library()
Show all papers in the library.
- Check what papers are available before searching for more

### 3. discover_papers(query, limit)
Search Semantic Scholar + paper-scraper for papers.
- Use AFTER querying library to fill knowledge gaps
- Returns: title, authors, year, abstract, arxiv_id, doi, citations
- Prioritize high-citation and recent papers

### 4. exa_search(query, limit)  
Neural search via Exa.ai. ⚠️ COSTS CREDITS!
- Only use when discover_papers doesn't find enough
- Good for conceptual/semantic queries

### 5. add_paper(identifier, source)
Download paper to library by arXiv ID or DOI.
- Updates master.bib automatically
- Add 5-10 MOST RELEVANT papers only

### 6. fuzzy_cite(query)
Find @citation_keys for papers in library.
- Fuzzy matches author, title, year
- Returns ONLY keys that actually exist
- ALWAYS use this before citing

### 7. validate_citations(citation_keys)
Validate a list of citation keys before writing.
- Returns valid/invalid keys and suggestions
- Use this to double-check your citations

## Workflow (ITERATIVE - RAG First)

1. **Query First**: query_library() with the main topic to see existing knowledge
2. **Identify Gaps**: Based on the answer, identify what's missing
3. **Search**: discover_papers() for specific gaps
4. **Acquire**: add_paper() for the most relevant papers found
5. **Query Again**: query_library() with more specific questions
6. **Repeat** steps 2-5 until you have comprehensive coverage
7. **Cite**: fuzzy_cite() for EACH paper you want to cite
8. **Validate**: validate_citations() to verify all keys exist
9. **Write**: Output complete Typst document

## Output Format

```typst
#import "lib.typ": project

#show: project.with(
  title: "Your Title",
  subtitle: "A Research Report",
  authors: ("Research Agent",),
  date: "CURRENT_DATE",
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

## ACADEMIC RIGOR RULES (STRICT)

### Citation Discipline
- NEVER make factual claims without a citation
- ONLY use @citation_keys that were returned by fuzzy_cite()
- If fuzzy_cite() returns no matches, DO NOT cite that paper
- Every paragraph should have at least one citation
- Use multiple citations when synthesizing across sources: @key1 @key2 @key3

### Research Integrity
- Base ALL claims on query_library() responses
- Do NOT invent facts, statistics, or paper claims
- If the library lacks information, explicitly state: "Further research is needed"
- Distinguish between what papers claim vs. what is established fact

### Critical Analysis
- Compare and contrast findings across papers
- Identify limitations and gaps in the literature
- Note methodological differences between studies
- Present multiple perspectives when they exist

### Writing Standards
- Use formal academic tone (no colloquialisms)
- Avoid hedging language unless uncertainty is warranted
- Be precise with terminology
- Define technical terms on first use

### Quality Checklist (verify before outputting)
1. Every factual claim has a citation
2. All @keys were verified via fuzzy_cite()
3. Abstract reflects actual findings from RAG
4. No speculative claims without caveats
5. Limitations are acknowledged"""


# ============================================================================
# AGENT LOOP
# ============================================================================

def run_agent(topic: str, research_plan: Optional[Dict[str, Any]] = None) -> str:
    """Run the research agent on a topic with optional research plan."""
    global _used_citation_keys
    _used_citation_keys = set()  # Reset for new run
    
    # Get current date for the document
    current_date = datetime.now().strftime("%B %Y")
    
    console.print(Panel(
        f"[bold cyan]🤖 Research Agent[/bold cyan]\n\n"
        f"[white]{topic}[/white]\n\n"
        f"[dim]Model: {AGENT_MODEL}[/dim]",
        border_style="cyan"
    ))
    
    # Inject current date into system prompt
    system_prompt_with_date = SYSTEM_PROMPT.replace("CURRENT_DATE", current_date)
    
    # Build user prompt with optional research plan
    plan_section = ""
    if research_plan:
        plan_section = f"""
RESEARCH PLAN:
- Main Question: {research_plan.get('main_question', topic)}
- Sub-questions: {', '.join(research_plan.get('sub_questions', [])[:3])}
- Key Concepts: {', '.join(research_plan.get('key_concepts', [])[:5])}
- Search Queries: {', '.join(research_plan.get('search_queries', [])[:3])}
"""
    
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"""Research this topic and produce a Typst document:

TOPIC: {topic}
{plan_section}
IMPORTANT - Follow the RAG-First workflow:
1. FIRST query_library() with the main topic to see what knowledge already exists
2. Identify gaps in the existing knowledge
3. discover_papers() to find papers addressing those gaps
4. add_paper() for the most relevant papers
5. query_library() again with more specific questions
6. Repeat until comprehensive
7. fuzzy_cite() to get @citation_keys
8. Output complete Typst document (use date: "{current_date}")""")]
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
                        system_instruction=system_prompt_with_date,
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


# ============================================================================
# RESEARCH PLANNER
# ============================================================================

PLANNER_PROMPT = """You are a research planning assistant. Given a topic, create a structured research plan.

Output a JSON research plan with:
1. "main_question": The central research question
2. "sub_questions": 3-5 specific questions to investigate
3. "key_concepts": Important terms/concepts to search for
4. "expected_sections": Proposed document structure
5. "search_queries": 3-4 specific search queries for academic databases

Be specific and academic. Output ONLY valid JSON, no markdown."""

def create_research_plan(topic: str) -> Dict[str, Any]:
    """Create a structured research plan before starting research."""
    console.print(Panel(
        f"[bold blue]📋 Research Planner[/bold blue]\n\n"
        f"Creating research plan for:\n[white]{topic}[/white]",
        border_style="blue"
    ))
    
    response = client.models.generate_content(
        model=AGENT_MODEL,
        contents=[types.Content(
            role="user",
            parts=[types.Part(text=f"Create a research plan for: {topic}")]
        )],
        config=types.GenerateContentConfig(
            system_instruction=PLANNER_PROMPT
        )
    )
    
    text = response.candidates[0].content.parts[0].text
    
    # Extract JSON
    try:
        # Try to find JSON in the response
        if "```json" in text:
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)
        elif "```" in text:
            json_match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)
        
        plan = json.loads(text)
        console.print("[green]✓ Research plan created[/green]")
        
        # Display plan
        console.print(f"\n[bold]Main Question:[/bold] {plan.get('main_question', 'N/A')}")
        if plan.get('sub_questions'):
            console.print("[bold]Sub-questions:[/bold]")
            for i, q in enumerate(plan['sub_questions'][:5], 1):
                console.print(f"  {i}. {q}")
        
        return plan
    except json.JSONDecodeError:
        console.print("[yellow]Could not parse plan, using defaults[/yellow]")
        return {
            "main_question": topic,
            "sub_questions": [topic],
            "key_concepts": [],
            "expected_sections": ["Introduction", "Background", "Analysis", "Conclusion"],
            "search_queries": [topic]
        }


# ============================================================================
# PEER REVIEWER
# ============================================================================

REVIEWER_PROMPT = """You are a rigorous academic peer reviewer. You review research documents with strict standards.

Your review must assess:

## 1. SCIENTIFIC RIGOR
- Are all claims properly supported by citations?
- Is the methodology (if applicable) sound?
- Are limitations acknowledged?

## 2. ARGUMENTATION
- Is the thesis clear and well-supported?
- Is the logical flow coherent?
- Are counter-arguments addressed?

## 3. CITATION QUALITY
- Are citations appropriate and sufficient?
- Are primary sources used where needed?
- Is there over-reliance on any single source?

## 4. COMPLETENESS
- Are all major aspects of the topic covered?
- Are there obvious gaps in the literature review?
- Is the scope appropriate?

## 5. WRITING QUALITY
- Is the academic tone consistent?
- Is technical terminology used correctly?
- Is the structure clear?

Output a STRUCTURED REVIEW with:
1. **Overall Assessment**: Accept / Minor Revisions / Major Revisions / Reject
2. **Strengths**: 2-3 bullet points
3. **Critical Issues**: Specific problems that MUST be fixed
4. **Suggestions**: Optional improvements
5. **Missing Content**: Topics/sources that should be added
6. **Specific Line Edits**: Concrete changes needed

Be constructive but rigorous. Academic excellence is the standard."""


def peer_review(document: str, topic: str, revision_num: int = 1) -> Dict[str, Any]:
    """Run peer review on a document. Returns structured feedback."""
    console.print(Panel(
        f"[bold magenta]🔍 Peer Review (Round {revision_num})[/bold magenta]\n\n"
        f"Reviewing document on: [white]{topic[:50]}...[/white]",
        border_style="magenta"
    ))
    
    response = client.models.generate_content(
        model=AGENT_MODEL,
        contents=[types.Content(
            role="user",
            parts=[types.Part(text=f"""Review this academic document:

TOPIC: {topic}

DOCUMENT:
```typst
{document}
```

Provide a structured peer review following your guidelines.""")]
        )],
        config=types.GenerateContentConfig(
            system_instruction=REVIEWER_PROMPT
        )
    )
    
    review_text = response.candidates[0].content.parts[0].text
    
    # Determine verdict
    verdict = "minor_revisions"
    if "Accept" in review_text and "Major" not in review_text:
        verdict = "accept"
    elif "Major Revisions" in review_text or "Major revisions" in review_text:
        verdict = "major_revisions"
    elif "Reject" in review_text:
        verdict = "reject"
    
    console.print(f"[bold]Verdict: {verdict.upper()}[/bold]")
    
    # Show summary
    lines = review_text.split('\n')[:15]
    for line in lines:
        if line.strip():
            console.print(f"[dim]{line}[/dim]")
    
    return {
        "verdict": verdict,
        "review": review_text,
        "revision_number": revision_num
    }


# ============================================================================
# REVISION AGENT
# ============================================================================

REVISION_PROMPT = """You are revising an academic document based on peer review feedback.

Your task:
1. Read the ORIGINAL document carefully
2. Study the PEER REVIEW feedback
3. Create an IMPLEMENTATION PLAN addressing each issue
4. Output the REVISED document

Address ALL critical issues raised. Maintain academic rigor.
Keep all existing citations but add more where needed.
Output the complete revised Typst document."""


def revise_document(original: str, review: str, topic: str, research_plan: Dict) -> str:
    """Revise document based on peer review feedback."""
    console.print(Panel(
        f"[bold yellow]✏️ Revision Phase[/bold yellow]\n\n"
        f"Addressing peer review feedback...",
        border_style="yellow"
    ))
    
    current_date = datetime.now().strftime("%B %Y")
    
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"""Revise this document based on peer review.

TOPIC: {topic}

RESEARCH PLAN:
{json.dumps(research_plan, indent=2)}

ORIGINAL DOCUMENT:
```typst
{original}
```

PEER REVIEW:
{review}

First, create a brief IMPLEMENTATION PLAN (3-5 bullet points).
Then output the complete REVISED Typst document.
Use date: "{current_date}" in the document.
""")]
        )
    ]
    
    # Run revision loop
    for iteration in range(15):
        with console.status(f"[cyan]Revising (step {iteration + 1})..."):
            try:
                response = client.models.generate_content(
                    model=AGENT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=REVISION_PROMPT,
                        tools=TOOLS
                    )
                )
            except Exception as e:
                console.print(f"[red]API error: {e}[/red]")
                return original
        
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
                    
                    console.print(f"[yellow]→ {func_name}({json.dumps(func_args, default=str)[:60]})[/yellow]")
                    
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
                
                if "#import" in text and "project.with" in text:
                    console.print("[green]✓ Revision complete[/green]")
                    if "```typst" in text:
                        match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                        if match:
                            return match.group(1).strip()
                    return text
                
                contents.append(types.Content(role="model", parts=parts))
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text="Continue. Output the complete revised Typst document.")]
                ))
    
    return original  # Fallback


# ============================================================================
# MULTI-PHASE ORCHESTRATOR
# ============================================================================

def generate_report(topic: str, max_revisions: int = 3) -> Path:
    """Generate a research report with planning, review, and revision phases."""
    global _used_citation_keys, _debug_logger
    _used_citation_keys = set()
    
    REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    
    # Create report directory early
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    topic_slug = "".join(c if c.isalnum() or c == " " else "" for c in topic[:40])
    topic_slug = topic_slug.strip().replace(" ", "_").lower()
    report_name = f"{timestamp}_{topic_slug}"
    report_dir = REPORTS_PATH / report_name
    report_dir.mkdir(parents=True, exist_ok=True)
    
    # Create artifacts subfolder
    artifacts_dir = report_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    # Setup debug logging
    _debug_logger = setup_debug_log(report_dir)
    log_debug(f"Starting research session: {topic}")
    log_debug(f"Max revisions: {max_revisions}")
    
    # ========== PHASE 1: PLANNING ==========
    console.print(Panel(
        f"[bold blue]Phase 1: Research Planning[/bold blue]",
        border_style="blue", width=60
    ))
    
    research_plan = create_research_plan(topic)
    
    # Save plan to artifacts
    (artifacts_dir / "research_plan.json").write_text(json.dumps(research_plan, indent=2))
    log_debug(f"Research plan created: {json.dumps(research_plan)}")
    
    # ========== PHASE 2: RESEARCH & WRITE ==========
    console.print(Panel(
        f"[bold cyan]Phase 2: Research & Writing[/bold cyan]",
        border_style="cyan", width=60
    ))
    
    typst_content = run_agent(topic, research_plan=research_plan)
    
    # Save initial draft
    (artifacts_dir / "draft_initial.typ").write_text(typst_content)
    log_debug("Initial draft complete")
    
    # ========== PHASE 3: PEER REVIEW LOOP ==========
    reviews = []
    
    for revision_round in range(1, max_revisions + 1):
        console.print(Panel(
            f"[bold magenta]Phase 3.{revision_round}: Peer Review[/bold magenta]",
            border_style="magenta", width=60
        ))
        
        review_result = peer_review(typst_content, topic, revision_round)
        reviews.append(review_result)
        
        # Save review to artifacts
        review_file = artifacts_dir / f"peer_review_r{revision_round}.md"
        review_file.write_text(f"# Peer Review - Round {revision_round}\n\n**Verdict**: {review_result['verdict'].upper()}\n\n{review_result['review']}")
        log_debug(f"Review {revision_round}: {review_result['verdict']}")
        
        # Check if accepted
        if review_result['verdict'] == 'accept':
            console.print("[bold green]✓ Paper accepted by reviewer![/bold green]")
            break
        
        # ========== PHASE 4: REVISION ==========
        console.print(Panel(
            f"[bold yellow]Phase 4.{revision_round}: Revision[/bold yellow]",
            border_style="yellow", width=60
        ))
        
        typst_content = revise_document(
            typst_content, 
            review_result['review'], 
            topic,
            research_plan
        )
        
        # Save draft to artifacts
        draft_file = artifacts_dir / f"draft_r{revision_round}.typ"
        draft_file.write_text(typst_content)
        log_debug(f"Revision {revision_round} complete")
    
    # ========== FINAL OUTPUT ==========
    console.print(Panel(
        f"[bold green]Finalizing Output[/bold green]",
        border_style="green", width=60
    ))
    
    # Copy template
    if (TEMPLATE_PATH / "lib.typ").exists():
        shutil.copy(TEMPLATE_PATH / "lib.typ", report_dir / "lib.typ")
    
    # Extract and filter citations
    doc_citations = extract_citations_from_typst(typst_content)
    all_cited = _used_citation_keys | doc_citations
    log_debug(f"Cited keys: {all_cited}")
    
    # Create filtered refs.bib
    if MASTER_BIB.exists():
        filtered_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
        (report_dir / "refs.bib").write_text(filtered_bib)
    else:
        (report_dir / "refs.bib").write_text("% No references\n")
    
    # Write final main.typ
    main_typ = report_dir / "main.typ"
    main_typ.write_text(typst_content)
    
    # Compile to PDF
    with console.status("[dim]Compiling PDF..."):
        try:
            result = subprocess.run(
                ["typst", "compile", "main.typ"],
                cwd=report_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                log_debug(f"Typst error: {result.stderr}")
        except FileNotFoundError:
            log_debug("typst not found")
        except Exception as e:
            log_debug(f"Compile error: {e}")
    
    # Summary
    console.print("\n" + "="*60)
    console.print(Panel(
        f"[bold green]✓ Research Complete[/bold green]\n\n"
        f"[white]Topic:[/white] {topic[:50]}...\n"
        f"[white]Reviews:[/white] {len(reviews)} rounds\n"
        f"[white]Final verdict:[/white] {reviews[-1]['verdict'].upper() if reviews else 'N/A'}\n\n"
        f"[dim]Output:[/dim]\n"
        f"  � main.typ\n"
        f"  � main.pdf\n"
        f"  📚 refs.bib\n"
        f"  📁 artifacts/ (plans, drafts, reviews)\n\n"
        f"[dim]{report_dir}[/dim]",
        border_style="green"
    ))
    
    log_debug(f"Session complete: {report_dir}")
    return report_dir


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Autonomous Research Agent with Peer Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  research agent "Impact of attention mechanisms on NLP"
  research agent -r 5 "Vision Transformers vs CNNs"
  research agent --revisions 1 "Quick research topic"
        """
    )
    parser.add_argument('topic', nargs='+', help='Research topic')
    parser.add_argument('--revisions', '-r', type=int, default=3,
                       help='Max peer review rounds (default: 3)')
    
    args = parser.parse_args()
    topic = " ".join(args.topic)
    
    if not topic:
        parser.print_help()
        sys.exit(1)
    
    if not os.getenv('GEMINI_API_KEY'):
        console.print("[red]Error: GEMINI_API_KEY not set[/red]")
        sys.exit(1)
    
    generate_report(topic, max_revisions=args.revisions)
