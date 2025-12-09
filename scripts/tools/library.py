"""
Library management tools.

Provides tools for managing the paper library:
- Adding papers via papis
- Listing papers in the library
- Querying papers with RAG (PaperQA2)
"""
import asyncio
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_PATH = REPO_ROOT / "library"
PAPIS_CONFIG = REPO_ROOT / "papis.config"
SCRIPTS_PATH = REPO_ROOT / "scripts"

# Model for RAG queries
FLASH_MODEL = "gemini-2.5-flash"

console = Console()

# Graceful external tool import for private PDF sources
try:
    from .external import fetch_pdf_private, PRIVATE_SOURCES_AVAILABLE
except ImportError:
    fetch_pdf_private = None
    PRIVATE_SOURCES_AVAILABLE = False


def add_paper(identifier: str, source: str = "auto") -> Dict[str, Any]:
    """
    Add a paper to the local library by its identifier.
    
    Downloads the PDF via papis and updates master.bib automatically.
    Falls back to private sources if papis can't get the PDF.
    Use this after finding relevant papers via discover_papers.
    
    Args:
        identifier: Paper identifier - either:
                   - arXiv ID (e.g., "1706.03762" for "Attention Is All You Need")
                   - DOI (e.g., "10.1038/nature12373")
        source: Source type - "arxiv", "doi", or "auto" (auto-detect from format)
    
    Returns:
        Dict with 'status' ("success" or "error") and 'citation_key' if successful
    """
    import shutil
    
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
    
    papis_success = False
    pdf_missing = False
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            papis_success = True
            
            # Check if PDF was actually downloaded
            # Look for recently added paper directory
            recent_dirs = sorted(LIBRARY_PATH.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
            if recent_dirs:
                latest_dir = recent_dirs[0]
                pdfs = list(latest_dir.glob("*.pdf"))
                if not pdfs:
                    pdf_missing = True
                    console.print(f"[yellow]⚠ Papis added metadata but no PDF found[/yellow]")
        else:
            console.print(f"[yellow]Papis failed: {result.stderr.strip()[:100]}[/yellow]")
    except subprocess.TimeoutExpired:
        console.print(f"[yellow]Papis timeout[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Papis error: {e}[/yellow]")
    
    # Fallback to private sources for PDF if needed
    if (pdf_missing or not papis_success) and source == "doi" and PRIVATE_SOURCES_AVAILABLE and fetch_pdf_private:
        console.print(f"[dim]Trying private sources for PDF...[/dim]")
        try:
            pdf_path = fetch_pdf_private(identifier)
            if pdf_path and pdf_path.exists():
                # If papis succeeded but no PDF, add to existing directory
                if papis_success and pdf_missing:
                    recent_dirs = sorted(LIBRARY_PATH.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
                    if recent_dirs:
                        target = recent_dirs[0] / f"{identifier.replace('/', '_')}.pdf"
                        shutil.move(str(pdf_path), str(target))
                        console.print(f"[green]✓ PDF added via private sources[/green]")
                else:
                    # Try to add via papis with the PDF we downloaded
                    cmd_with_pdf = [papis_cmd, "--config", str(PAPIS_CONFIG), "-l", "main", 
                                   "add", "--batch", "--from", source, identifier, 
                                   "--set", "files", str(pdf_path)]
                    result = subprocess.run(cmd_with_pdf, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        papis_success = True
                        console.print(f"[green]✓ Added via private sources[/green]")
                    # Clean up temp PDF
                    pdf_path.unlink(missing_ok=True)
        except Exception as e:
            console.print(f"[dim]Private sources failed: {e}[/dim]")
    
    if papis_success:
        # Sync master.bib using existing utility
        try:
            sys.path.insert(0, str(SCRIPTS_PATH))
            from utils.sync_bib import sync_master_bib
            sync_master_bib()
        except Exception as e:
            console.print(f"[yellow]Warning: bib sync issue: {e}[/yellow]")
        
        console.print(f"[green]✓ Added {identifier}[/green]")
        return {"status": "success", "identifier": identifier}
    else:
        return {"status": "error", "message": "Failed to add paper from any source"}



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
