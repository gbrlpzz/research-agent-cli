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


def get_used_citation_keys() -> Set[str]:
    """Get the set of citation keys that have been used."""
    return _used_citation_keys


def clear_used_citation_keys():
    """Clear the tracked citation keys (call at start of new session)."""
    global _used_citation_keys
    _used_citation_keys = set()


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
