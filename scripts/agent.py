#!/usr/bin/env python3
"""
Autonomous Research Agent
=========================

An agentic research assistant that autonomously:
1. Plans research by decomposing topics into questions
2. Searches for academic papers via Semantic Scholar
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
    ├── refs.bib     # Citations (from master.bib)
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
from typing import Optional, List, Dict, Any

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

# ============================================================================
# TOOL FUNCTIONS - Wrapping existing CLI infrastructure
# ============================================================================

def search_papers(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search for academic papers using Semantic Scholar.
    
    Use this tool FIRST to discover relevant papers on a topic before
    adding them to the library.
    
    Args:
        query: Search query string describing the research topic
               (e.g., "transformer attention mechanism", "vision transformers")
        limit: Maximum number of results to return (default: 10)
    
    Returns:
        List of paper metadata with: title, authors, year, abstract, arxiv_id, doi, citations
    """
    from semanticscholar import SemanticScholar
    import itertools
    
    console.print(f"[dim]🔍 Searching: {query}[/dim]")
    sch = SemanticScholar()
    
    try:
        results = sch.search_paper(query, limit=limit)
        papers = []
        for paper in itertools.islice(results, limit):
            arxiv_id = None
            doi = None
            if paper.externalIds:
                arxiv_id = paper.externalIds.get('ArXiv')
                doi = paper.externalIds.get('DOI')
            
            papers.append({
                'title': paper.title,
                'authors': [a.name for a in paper.authors][:3],
                'year': paper.year,
                'abstract': paper.abstract[:500] if paper.abstract else None,
                'arxiv_id': arxiv_id,
                'doi': doi,
                'citations': paper.citationCount or 0
            })
        
        console.print(f"[green]✓ Found {len(papers)} papers[/green]")
        return papers
    except Exception as e:
        console.print(f"[red]Search error: {e}[/red]")
        return []


def add_paper(identifier: str, source: str = "auto") -> Dict[str, Any]:
    """
    Add a paper to the local library by its identifier.
    
    Downloads the PDF via papis and updates master.bib automatically.
    Use this after finding relevant papers via search_papers.
    
    Args:
        identifier: Paper identifier - either:
                   - arXiv ID (e.g., "1706.03762" for "Attention Is All You Need")
                   - DOI (e.g., "10.1038/nature12373")
        source: Source type - "arxiv", "doi", or "auto" (auto-detect from format)
    
    Returns:
        Dict with 'status' ("success" or "error") and details
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
    
    # Generate fingerprint
    fingerprint_data = sorted([f"{p}:{p.stat().st_mtime}" for p in pdfs])
    current_fp = hashlib.md5("\n".join(fingerprint_data).encode()).hexdigest()
    
    docs = None
    use_cached = False
    
    # Try to load existing store
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
                    use_cached = True
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
        for pdf in pdfs[:20]:  # Limit for performance
            try:
                docs.add(pdf, settings=settings)
            except:
                pass
        
        # Save fingerprint
        if not paper_filter:
            fp_path.parent.mkdir(exist_ok=True)
            fp_path.write_text(current_fp)
    
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


def get_citation_keys(query: str) -> List[Dict[str, str]]:
    """
    Search the library for papers and return their citation keys.
    
    Use these keys with @key syntax in the final Typst document.
    
    Args:
        query: Search term to match against papers
               (author name, title keyword, year, etc.)
    
    Returns:
        List of dicts with: citation_key, title, authors, year
        Use the citation_key as @citation_key in the document
    """
    import yaml
    
    console.print(f"[dim]📚 Finding citations: {query}[/dim]")
    
    results = []
    query_lower = query.lower()
    
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            
            searchable = f"{data.get('ref', '')} {data.get('title', '')} {data.get('author', '')}".lower()
            
            if query_lower in searchable:
                results.append({
                    "citation_key": data.get('ref', 'unknown'),
                    "title": data.get('title', 'Unknown')[:80],
                    "authors": data.get('author', 'Unknown')[:50],
                    "year": str(data.get('year', ''))
                })
        except:
            pass
    
    console.print(f"[green]✓ Found {len(results)} citations[/green]")
    return results[:15]


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
                "title": data.get('title', 'Unknown')[:80],
                "authors": data.get('author', 'Unknown')[:50],
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
            name="search_papers",
            description="Search for academic papers on Semantic Scholar. Use FIRST to discover relevant papers before adding them.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search query for the topic"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Max results (default 10)")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="add_paper",
            description="Add a paper to the library by arXiv ID or DOI. Downloads PDF and updates master.bib. Use after search_papers.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "identifier": types.Schema(type=types.Type.STRING, description="arXiv ID (e.g., '1706.03762') or DOI (e.g., '10.1038/...')"),
                    "source": types.Schema(type=types.Type.STRING, description="'arxiv', 'doi', or 'auto' (default: auto)")
                },
                required=["identifier"]
            )
        ),
        types.FunctionDeclaration(
            name="query_library",
            description="Ask a research question using RAG (PaperQA2) with persistent vector store. Returns detailed answer with citations. Use AFTER adding papers.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "question": types.Schema(type=types.Type.STRING, description="Specific research question to answer"),
                    "paper_filter": types.Schema(type=types.Type.STRING, description="Optional filter by keyword (author, title word)")
                },
                required=["question"]
            )
        ),
        types.FunctionDeclaration(
            name="get_citation_keys",
            description="Find @citation_keys for papers in library to use in the Typst document. Search by author, title, or year.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search term (author name, title keyword, year)")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="list_library",
            description="List all papers currently in the library. Use to see what's available before adding more.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
                required=[]
            )
        )
    ])
]

TOOL_FUNCTIONS = {
    "search_papers": search_papers,
    "add_paper": add_paper,
    "query_library": query_library,
    "get_citation_keys": get_citation_keys,
    "list_library": list_library
}

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are an autonomous research agent that produces comprehensive academic Typst documents.

## Your Mission
Given a research topic, you will:
1. Search for relevant papers
2. Add the most important ones to the library
3. Query the library using RAG to synthesize information
4. Generate a complete, well-cited Typst document

## Available Tools

### 1. search_papers(query, limit)
Search Semantic Scholar for academic papers.
- Use FIRST to discover relevant work on the topic
- Returns: title, authors, year, abstract, arxiv_id, doi, citations
- Prioritize papers with high citation counts and recent publication dates

### 2. add_paper(identifier, source)
Download a paper to the library. Uses papis and updates master.bib.
- Input: arXiv ID (e.g., "1706.03762") or DOI
- Only add the 5-10 MOST RELEVANT papers (quality over quantity)
- Wait for each add to complete before querying

### 3. query_library(question, paper_filter)
Ask questions about library papers using PaperQA2 RAG.
- Uses persistent Qdrant vector database for fast queries
- Returns detailed answers with source citations
- Use AFTER adding papers to get synthesized insights

### 4. get_citation_keys(query)
Find @citation_keys for papers in the library.
- Search by author name, title keyword, or year
- Use these keys in the Typst document as @citation_key

### 5. list_library()
List all papers currently available in the library.
- Use to check existing resources before adding more

## Workflow

1. **Explore**: Use list_library() to see existing papers
2. **Search**: Use search_papers() for 2-3 targeted queries on the topic
3. **Acquire**: Use add_paper() for the 5-8 most important papers found
4. **Research**: Use query_library() multiple times with specific questions:
   - "What is the main contribution of X?"
   - "How does Y compare to Z?"
   - "What are the limitations of this approach?"
5. **Cite**: Use get_citation_keys() to find proper keys for papers you reference
6. **Write**: Generate the complete Typst document

## Output Format

When you have gathered sufficient information, output the COMPLETE Typst document.
The document MUST follow this exact structure:

```typst
#import "lib.typ": project

#show: project.with(
  title: "Your Descriptive Title",
  subtitle: "A Research Report",
  authors: ("Research Agent",),
  date: "December 2025",
  abstract: [
    A concise abstract summarizing the topic, key findings, and conclusions.
    Should be 3-5 sentences.
  ]
)

#outline(indent: auto)
#pagebreak()

= Introduction
Opening paragraph introducing the topic and its importance.
Brief overview of what the document covers.

= Background
Foundational concepts needed to understand the topic.
Use citations like @citation_key throughout.

= Main Section Title
Core content with multiple subsections as needed.

== Subsection
Detailed analysis and synthesis of the literature.

= Discussion
Analysis of findings, implications, and connections.

= Conclusion
Summary of key points and potential future directions.

#bibliography("refs.bib")
```

## Citation Rules
- ALWAYS use @citation_key format for citations
- Only cite papers that are actually in the library
- Use get_citation_keys() to verify the exact key format
- Integrate citations naturally into the text

## Quality Standards
- Be thorough and scholarly in tone
- Support all claims with citations
- Synthesize information across multiple sources
- Include critical analysis, not just summaries
- Aim for 1500-2500 words of content"""

# ============================================================================
# AGENT LOOP
# ============================================================================

def run_agent(topic: str) -> str:
    """
    Run the research agent on a topic.
    
    Args:
        topic: Research topic to investigate
        
    Returns:
        Typst document content as a string
    """
    console.print(Panel(
        f"[bold cyan]🤖 Research Agent[/bold cyan]\n\n"
        f"[white]{topic}[/white]\n\n"
        f"[dim]Model: {AGENT_MODEL}[/dim]",
        border_style="cyan"
    ))
    
    # Build conversation with user message
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"""Research the following topic and produce a comprehensive academic document:

TOPIC: {topic}

Begin by exploring the current library, then search for and add relevant papers.
Use the query_library tool to synthesize information from the papers.
Finally, output a complete, well-structured Typst document with proper citations.""")]
        )
    ]
    
    console.print("\n[bold]Starting autonomous research...[/bold]\n")
    
    max_iterations = 30
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
        
        # Check for function calls
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]
            
            if function_calls:
                # Add assistant response to conversation
                contents.append(types.Content(
                    role="model",
                    parts=parts
                ))
                
                # Execute each function call
                function_response_parts = []
                for part in function_calls:
                    fc = part.function_call
                    func_name = fc.name
                    func_args = dict(fc.args) if fc.args else {}
                    
                    console.print(f"[yellow]→ {func_name}({json.dumps(func_args, default=str)[:100]})[/yellow]")
                    
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
                
                # Add function responses
                contents.append(types.Content(
                    role="user",
                    parts=function_response_parts
                ))
            else:
                # No function calls - check for final document
                text = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
                
                # Check if this is a complete Typst document
                if "#import" in text and "project.with" in text and "#bibliography" in text:
                    console.print("[green]✓ Document generated[/green]")
                    # Extract the typst code block if wrapped
                    if "```typst" in text:
                        match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                        if match:
                            return match.group(1).strip()
                    return text
                
                # Show partial response
                console.print(f"[dim]{text[:200]}...[/dim]")
                
                # Prompt to continue
                contents.append(types.Content(
                    role="model",
                    parts=parts
                ))
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text="Continue with the next step. When you have sufficient research, output the complete Typst document.")]
                ))
        else:
            console.print("[yellow]Empty response, retrying...[/yellow]")
    
    console.print("[yellow]Warning: Reached iteration limit[/yellow]")
    return "// Agent did not produce a document within iteration limit"


def generate_report(topic: str) -> Path:
    """
    Generate a complete research report on a topic.
    
    Args:
        topic: Research topic
        
    Returns:
        Path to the generated report directory
    """
    # Ensure reports directory exists
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
        console.print(f"[dim]Copied lib.typ from template[/dim]")
    else:
        console.print(f"[yellow]Warning: Template not found at {TEMPLATE_PATH}[/yellow]")
    
    # Copy master.bib as refs.bib
    if MASTER_BIB.exists():
        shutil.copy(MASTER_BIB, report_dir / "refs.bib")
        console.print(f"[dim]Copied master.bib as refs.bib[/dim]")
    else:
        (report_dir / "refs.bib").write_text("% No references available\n")
        console.print(f"[yellow]Warning: master.bib not found[/yellow]")
    
    # Write main.typ
    main_typ = report_dir / "main.typ"
    main_typ.write_text(typst_content)
    console.print(f"[green]✓ Created {main_typ.name}[/green]")
    
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
            console.print(f"[yellow]Compilation warning: {result.stderr[:200]}[/yellow]")
    except FileNotFoundError:
        console.print("[yellow]typst not found - install with: brew install typst[/yellow]")
    except subprocess.TimeoutExpired:
        console.print("[yellow]Compilation timeout[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Compilation error: {e}[/yellow]")
    
    # Summary
    console.print(Panel(
        f"[bold green]📄 Report Generated[/bold green]\n\n"
        f"📁 Location: [cyan]{report_dir}[/cyan]\n"
        f"� Document: main.typ\n"
        f"📚 References: refs.bib\n"
        f"📖 PDF: main.pdf",
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
  research agent "Vision Transformers vs CNNs for image classification"
  research agent "Few-shot learning in large language models"
  
The agent will:
  1. Search for relevant papers on Semantic Scholar
  2. Add the best papers to the library (with PDFs)
  3. Query the papers using PaperQA2 RAG
  4. Generate a complete Typst document with citations
  5. Compile to PDF (requires: brew install typst)
        """
    )
    parser.add_argument('topic', nargs='+', help='Research topic to investigate')
    
    args = parser.parse_args()
    topic = " ".join(args.topic)
    
    if not topic:
        parser.print_help()
        sys.exit(1)
    
    if not os.getenv('GEMINI_API_KEY'):
        console.print("[red]Error: GEMINI_API_KEY not set in .env[/red]")
        console.print("Get a free key at: https://aistudio.google.com/app/apikey")
        sys.exit(1)
    
    generate_report(topic)
