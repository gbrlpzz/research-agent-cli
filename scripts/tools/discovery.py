"""
Paper discovery tools.

Provides unified paper discovery across multiple sources:
- Semantic Scholar (~200M papers, citation counts)
- Paper-scraper (PubMed, bioRxiv, Springer, arXiv)
- Exa.ai (neural/semantic search, costs credits)
"""
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_PATH = REPO_ROOT / "scripts"

console = Console()

# Graceful external tool import
try:
    from .external import discover_via_private, PRIVATE_SOURCES_AVAILABLE
except ImportError:
    discover_via_private = None
    PRIVATE_SOURCES_AVAILABLE = False


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
    import concurrent.futures
    
    console.print(f"[dim]🔍 Unified search: {query}[/dim]")
    
    papers = []
    seen_ids = set()
    
    # 1. Search Semantic Scholar (with timeout to prevent blocking)
    console.print("[dim]  → Semantic Scholar...[/dim]")
    
    # Use API key if available for higher rate limits
    s2_api_key = os.getenv('SEMANTIC_SCHOLAR_API_KEY')
    
    def _search_s2():
        """Inner function to run S2 search (can be timed out)."""
        sch = SemanticScholar(api_key=s2_api_key) if s2_api_key else SemanticScholar()
        s2_papers = []
        results = sch.search_paper(query, limit=limit)
        for paper in itertools.islice(results, limit):
            arxiv_id = None
            doi = None
            if paper.externalIds:
                arxiv_id = paper.externalIds.get('ArXiv')
                doi = paper.externalIds.get('DOI')
            s2_papers.append({
                'title': paper.title,
                'authors': [a.name for a in paper.authors][:3],
                'year': paper.year,
                'abstract': paper.abstract[:400] if paper.abstract else None,
                'arxiv_id': arxiv_id,
                'doi': doi,
                'citations': paper.citationCount or 0,
                'source': 'S2'
            })
        return s2_papers
    
    try:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_search_s2)
        try:
            s2_results = future.result(timeout=30)  # 30 second timeout for S2
            for paper in s2_results:
                key = paper['doi'] or paper['arxiv_id'] or paper['title'][:50]
                if key not in seen_ids:
                    seen_ids.add(key)
                    papers.append(paper)
            executor.shutdown(wait=False)
        except concurrent.futures.TimeoutError:
            console.print("[dim]S2 timed out, continuing with other sources[/dim]")
            executor.shutdown(wait=False)  # Don't wait for hung thread
    except Exception as e:
        error_msg = str(e) if str(e) else type(e).__name__
        console.print(f"[yellow]S2 error: {error_msg}[/yellow]")
    
    # 2. Search paper-scraper (with timeout to prevent blocking)
    console.print("[dim]  → paper-scraper...[/dim]")
    try:
        sys.path.insert(0, str(SCRIPTS_PATH))
        from utils import scraper_client
        
        # Run with timeout to prevent blocking on network issues
        ps_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ps_executor.submit(scraper_client.search_papers, query, 10)
        try:
            ps_results = future.result(timeout=5)  # 5 second timeout (fast fail)
            ps_executor.shutdown(wait=False)
        except concurrent.futures.TimeoutError:
            console.print("[dim]paper-scraper timed out, continuing with S2 results[/dim]")
            ps_executor.shutdown(wait=False)  # Don't wait for hung thread
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
