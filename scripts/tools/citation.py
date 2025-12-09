"""
Citation management tools.

Provides tools for managing and validating citations:
- Fuzzy search for citation keys
- Validation of citation keys against the library
"""
from pathlib import Path
from typing import Any, Dict, List, Set

from rich.console import Console

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_PATH = REPO_ROOT / "library"

console = Console()

# Global state for tracking which papers were used during agent sessions
_used_citation_keys: Set[str] = set()

# Extended tracking: all papers reviewed with relevance/utility info
# {citation_key: {title, authors, year, relevance, utility, cited, source}}
_reviewed_papers: Dict[str, Dict[str, Any]] = {}


def get_used_citation_keys() -> Set[str]:
    """Get the set of citation keys that have been used."""
    return _used_citation_keys


def clear_used_citation_keys():
    """Clear the tracked citation keys (call at start of new session)."""
    global _used_citation_keys, _reviewed_papers
    _used_citation_keys = set()
    _reviewed_papers = {}


def track_reviewed_paper(
    citation_key: str,
    title: str,
    authors: str,
    year: str,
    relevance: int = 3,  # 1-5 scale
    utility: int = 3,    # 1-5 scale
    source: str = "discovery"
):
    """Track a paper that was reviewed during the research process."""
    global _reviewed_papers
    _reviewed_papers[citation_key] = {
        "title": title,
        "authors": authors,
        "year": year,
        "relevance": relevance,
        "utility": utility,
        "cited": citation_key in _used_citation_keys,
        "source": source
    }


def update_cited_status():
    """Update the 'cited' flag for all reviewed papers based on used keys."""
    global _reviewed_papers
    for key in _reviewed_papers:
        _reviewed_papers[key]["cited"] = key in _used_citation_keys


def get_reviewed_papers() -> Dict[str, Dict[str, Any]]:
    """Get all papers that were reviewed with their metadata."""
    update_cited_status()
    return _reviewed_papers


def export_literature_sheet() -> str:
    """
    Export a markdown literature review sheet with all papers reviewed.
    
    Shows relevance/utility rankings and highlights cited papers.
    """
    update_cited_status()
    
    if not _reviewed_papers:
        return "# Literature Review\n\nNo papers reviewed.\n"
    
    lines = [
        "# Literature Review Sheet",
        "",
        "This document lists all papers reviewed during the research process.",
        "",
        "## Summary",
        f"- **Total Papers Reviewed**: {len(_reviewed_papers)}",
        f"- **Papers Cited**: {sum(1 for p in _reviewed_papers.values() if p['cited'])}",
        "",
        "---",
        "",
        "## Papers Cited in Final Document",
        "",
        "| Citation Key | Title | Authors | Year | Rel | Util |",
        "|-------------|-------|---------|------|-----|------|"
    ]
    
    # Sort by relevance then utility
    sorted_papers = sorted(
        _reviewed_papers.items(),
        key=lambda x: (-x[1].get("relevance", 0), -x[1].get("utility", 0))
    )
    
    # Cited papers first
    for key, data in sorted_papers:
        if data.get("cited"):
            lines.append(
                f"| `{key}` | {data['title'][:50]}{'...' if len(data['title']) > 50 else ''} | "
                f"{data['authors'][:30]} | {data['year']} | {data['relevance']}/5 | {data['utility']}/5 |"
            )
    
    lines.extend([
        "",
        "## Papers Reviewed but Not Cited",
        "",
        "| Citation Key | Title | Authors | Year | Rel | Util | Source |",
        "|-------------|-------|---------|------|-----|------|--------|"
    ])
    
    for key, data in sorted_papers:
        if not data.get("cited"):
            lines.append(
                f"| `{key}` | {data['title'][:40]}{'...' if len(data['title']) > 40 else ''} | "
                f"{data['authors'][:25]} | {data['year']} | {data['relevance']}/5 | {data['utility']}/5 | {data['source']} |"
            )
    
    lines.extend([
        "",
        "---",
        "",
        "*Relevance (Rel): How relevant to the research topic (1-5)*",
        "",
        "*Utility (Util): How useful for answering research questions (1-5)*"
    ])
    
    return "\n".join(lines)


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
                title = data.get('title', 'Unknown')
                authors = data.get('author', 'Unknown')
                year = str(data.get('year', ''))
                
                results.append({
                    "citation_key": citation_key,
                    "title": title[:70],
                    "authors": authors[:40],
                    "year": year
                })
                # Track for refs.bib filtering
                _used_citation_keys.add(citation_key)
                
                # Track in reviewed papers with high relevance (since it matched query)
                if citation_key not in _reviewed_papers:
                    _reviewed_papers[citation_key] = {
                        "title": title,
                        "authors": authors[:60],
                        "year": year,
                        "relevance": 4,  # High since it matched fuzzy query
                        "utility": 4,    # High since agent requested it
                        "cited": True,
                        "source": "fuzzy_cite"
                    }
                else:
                    # Update existing entry to mark as cited
                    _reviewed_papers[citation_key]["cited"] = True
        except:
            pass
    
    # If no matches found, suggest a discovery search
    if not results:
        console.print(f"[yellow]⚠ No matches for '{query}' - consider using discover_papers[/yellow]")
        # Return a hint for the agent
        return [{
            "citation_key": None,
            "title": f"No matches for '{query}'",
            "authors": "Use discover_papers to find and add_paper to library first",
            "year": "",
            "suggestion": f"discover_papers(\"{query}\")"
        }]
    
    console.print(f"[green]✓ Found {len(results)} matches[/green]")
    return results[:10]



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
