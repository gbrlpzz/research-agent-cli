"""
Paper-scraper client module for multi-publisher paper search.
Provides unified interface for searching PubMed, arXiv, bioRxiv, Springer, etc.
"""
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Optional
import logging

try:
    import paperscraper
except ImportError:
    paperscraper = None

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if paper-scraper is available."""
    if paperscraper is None:
        logger.debug("paper-scraper not installed")
        return False
    return True


def search_papers(query: str, limit: int = 20) -> List[Dict]:
    """
    Search papers using paper-scraper.
    
    Args:
        query: Search query string
        limit: Maximum number of results
    
    Returns:
        List of paper dicts in standardized format:
        [{
            'source': 'paperscraper',
            'title': str,
            'authors': List[str],
            'year': int | None,
            'doi': str | None,
            'arxiv_id': str | None,
            'url': str | None,
            'pdf_path': str | None,  # Local path if PDF downloaded
            'abstract': str | None,
        }]
    """
    if not is_available():
        return []
    
    logger.info(f"Searching paper-scraper for: {query}")
    
    try:
        # Create temp directory for downloads
        with tempfile.TemporaryDirectory() as tmpdir:
            # Search papers (returns dict of {path: metadata})
            papers_dict = paperscraper.search_papers(query, limit=limit, pdir=tmpdir)
            
            results = []
            for pdf_path, metadata in papers_dict.items():
                # Extract metadata from paper-scraper results
                paper = {
                    'source': 'paperscraper',
                    'title': metadata.get('title', 'Untitled'),
                    'authors': _parse_authors(metadata.get('author', '')),
                    'year': _extract_year(metadata),
                    'doi': metadata.get('doi'),
                    'arxiv_id': _extract_arxiv_id(metadata),
                    'url': metadata.get('url'),
                    'pdf_path': pdf_path if os.path.exists(pdf_path) else None,
                    'abstract': metadata.get('abstract'),
                }
                results.append(paper)
                
                logger.debug(f"Found paper: {paper['title']}")
            
            logger.info(f"Found {len(results)} papers from paper-scraper")
            return results
            
    except Exception as e:
        logger.error(f"Error searching paper-scraper: {e}")
        return []


def _parse_authors(author_str: str) -> List[str]:
    """Parse author string into list of author names."""
    if not author_str:
        return []
    
    # Handle common author separators
    if ' and ' in author_str:
        return [a.strip() for a in author_str.split(' and ')]
    elif ',' in author_str:
        return [a.strip() for a in author_str.split(',')]
    else:
        return [author_str.strip()]


def _extract_year(metadata: dict) -> Optional[int]:
    """Extract publication year from metadata."""
    year_str = metadata.get('year') or metadata.get('date', '')
    
    if not year_str:
        return None
    
    # Try to extract 4-digit year
    import re
    match = re.search(r'\b(19|20)\d{2}\b', str(year_str))
    if match:
        return int(match.group())
    
    return None


def _extract_arxiv_id(metadata: dict) -> Optional[str]:
    """Extract arXiv ID from metadata if present."""
    url = metadata.get('url', '')
    eprint = metadata.get('eprint', '')
    
    # Check eprint field first
    if eprint:
        return eprint
    
    # Try to extract from URL
    if 'arxiv.org' in url:
        import re
        match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', url)
        if match:
            return match.group(1)
    
    return None
