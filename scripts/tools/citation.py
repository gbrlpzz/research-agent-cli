"""
Citation management tools.

Provides tools for managing and validating citations:
- Fuzzy search for citation keys
- Validation of citation keys against the library
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_PATH = REPO_ROOT / "library"

console = Console()

# Global state for tracking which papers were used during agent sessions
_used_citation_keys: Set[str] = set()

# Extended tracking: all items encountered in the session (including discovered-but-not-added).
# Keyed by a stable paper_id, not necessarily a library citation key.
# paper_id examples:
#   - cite:<citation_key>
#   - doi:<doi>
#   - arxiv:<arxiv_id>
#   - titlehash:<hash>
_reviewed_papers: Dict[str, Dict[str, Any]] = {}


def _stable_title_hash(title: str) -> str:
    import hashlib
    t = (title or "").strip().lower().encode("utf-8")
    return hashlib.sha1(t).hexdigest()[:10]


def make_paper_id(
    *,
    citation_key: Optional[str] = None,
    doi: Optional[str] = None,
    arxiv_id: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    if citation_key:
        return f"cite:{citation_key.strip()}"
    if doi:
        return f"doi:{doi.strip()}"
    if arxiv_id:
        return f"arxiv:{arxiv_id.strip()}"
    if title:
        return f"titlehash:{_stable_title_hash(title)}"
    return "unknown"


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
    source: str = "discovery",
    doi: Optional[str] = None,
    arxiv_id: Optional[str] = None,
    citations: Optional[int] = None,
    used_as_evidence: bool = False,
):
    """Track a paper that was reviewed during the research process."""
    global _reviewed_papers
    paper_id = make_paper_id(citation_key=citation_key, doi=doi, arxiv_id=arxiv_id, title=title)

    existing = _reviewed_papers.get(paper_id, {})
    prev_rel = int(existing.get("relevance", 0) or 0)
    prev_util = int(existing.get("utility", 0) or 0)

    _reviewed_papers[paper_id] = {
        "paper_id": paper_id,
        "citation_key": citation_key or existing.get("citation_key", ""),
        "doi": doi or existing.get("doi", ""),
        "arxiv_id": arxiv_id or existing.get("arxiv_id", ""),
        "citations": citations if citations is not None else existing.get("citations"),
        "title": title or existing.get("title", ""),
        "authors": authors or existing.get("authors", ""),
        "year": year or existing.get("year", ""),
        "relevance": max(prev_rel, int(relevance or 0)),
        "utility": max(prev_util, int(utility or 0)),
        "cited": (citation_key in _used_citation_keys) if citation_key else bool(existing.get("cited")),
        "used_as_evidence": bool(existing.get("used_as_evidence")) or bool(used_as_evidence),
        "source": source or existing.get("source", ""),
    }


def mark_used_as_evidence(*, citation_key: Optional[str] = None, title: Optional[str] = None, source: str = "query_library") -> None:
    """
    Mark a paper as having been used as evidence in a RAG answer.
    Best-effort matching by citation_key (preferred) or title.
    """
    global _reviewed_papers

    if citation_key:
        pid = make_paper_id(citation_key=citation_key)
        if pid in _reviewed_papers:
            _reviewed_papers[pid]["used_as_evidence"] = True
            _reviewed_papers[pid]["source"] = _reviewed_papers[pid].get("source") or source
            return

    if title:
        pid = make_paper_id(title=title)
        if pid not in _reviewed_papers:
            _reviewed_papers[pid] = {
                "paper_id": pid,
                "citation_key": "",
                "doi": "",
                "arxiv_id": "",
                "citations": None,
                "title": title,
                "authors": "",
                "year": "",
                "relevance": 3,
                "utility": 3,
                "cited": False,
                "used_as_evidence": True,
                "source": source,
            }
        else:
            _reviewed_papers[pid]["used_as_evidence"] = True


def update_cited_status():
    """Update the 'cited' flag for all reviewed papers based on used keys."""
    global _reviewed_papers
    for pid, data in _reviewed_papers.items():
        ck = data.get("citation_key") or ""
        if ck:
            data["cited"] = ck in _used_citation_keys


def get_reviewed_papers() -> Dict[str, Dict[str, Any]]:
    """Get all papers that were reviewed with their metadata."""
    update_cited_status()
    return _reviewed_papers


def export_literature_sheet() -> str:
    """
    Export a CSV literature review sheet with all papers reviewed.
    
    Format: citation_key,title,authors,year,relevance,utility,cited,source
    """
    update_cited_status()
    
    # Keep the CSV focused on decision-making:
    # - identifiers (so you can add it)
    # - title/year (so you can recognize it)
    # - relevance + used flags (so you can prioritize)
    # - source/citations (lightweight provenance)
    if not _reviewed_papers:
        return "paper_id,citation_key,doi,arxiv_id,title,year,relevance,used,cited,used_as_evidence,source,citations\n"
    
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(
        [
            "paper_id",
            "citation_key",
            "doi",
            "arxiv_id",
            "title",
            "year",
            "relevance",
            "used",
            "cited",
            "used_as_evidence",
            "source",
            "citations",
        ]
    )
    
    # Sort by whether used (cited or evidence), then relevance/utility
    sorted_papers = sorted(
        _reviewed_papers.items(),
        key=lambda x: (
            0 if (x[1].get("cited") or x[1].get("used_as_evidence")) else 1,
            0 if x[1].get("cited") else 1,
            -int(x[1].get("relevance", 0) or 0),
            -int(x[1].get("utility", 0) or 0),
            str(x[1].get("year") or ""),
            x[0],
        ),
    )
    
    for _, data in sorted_papers:
        writer.writerow([
            data.get("paper_id", ""),
            data.get("citation_key", ""),
            data.get("doi", ""),
            data.get("arxiv_id", ""),
            data.get("title", ""),
            data.get("year", ""),
            data.get("relevance", 0),
            "true" if (data.get("cited") or data.get("used_as_evidence")) else "false",
            "true" if data.get("cited") else "false",
            "true" if data.get("used_as_evidence") else "false",
            data.get("source", ""),
            data.get("citations", ""),
        ])
        
    return output.getvalue()


def export_literature_sheet_markdown(limit: int = 200) -> str:
    """
    Export a Markdown table literature sheet (human-readable).
    This is useful for quick inspection in reports/.
    """
    update_cited_status()

    header = (
        "| paper_id | citation_key | title | year | relevance | used | cited | used_as_evidence | source |\n"
        "|---|---|---|---:|---:|---|---|---|---|\n"
    )
    if not _reviewed_papers:
        return header

    sorted_papers = sorted(
        _reviewed_papers.items(),
        key=lambda x: (
            0 if (x[1].get("cited") or x[1].get("used_as_evidence")) else 1,
            0 if x[1].get("cited") else 1,
            -int(x[1].get("relevance", 0) or 0),
            x[0],
        ),
    )

    lines = [header]
    for key, data in sorted_papers[: max(1, int(limit))]:
        def esc(v: Any) -> str:
            s = str(v or "")
            return s.replace("|", "\\|").replace("\n", " ").strip()

        lines.append(
            f"| {esc(data.get('paper_id', key))} | {esc(data.get('citation_key'))} | {esc(data.get('title'))} | "
            f"{esc(data.get('year'))} | {data.get('relevance', 0)} | "
            f"{'yes' if (data.get('cited') or data.get('used_as_evidence')) else 'no'} | "
            f"{'yes' if data.get('cited') else 'no'} | {'yes' if data.get('used_as_evidence') else 'no'} | {esc(data.get('source'))} |\\n"
        )

    return "".join(lines)


def literature_sheet(limit: int = 20, only_uncited: bool = False, verbose: bool = False) -> Dict[str, Any]:
    """
    Tool-friendly access to the current session's literature sheet.

    Returns a JSON-like dict (summary + rows) so the agent can reason about:
    - what has been reviewed/added/cited
    - what is uncited
    - what looks high relevance/utility
    """
    update_cited_status()

    items = sorted(
        _reviewed_papers.items(),
        key=lambda x: (-x[1].get("relevance", 0), -x[1].get("utility", 0), x[0]),
    )
    if only_uncited:
        items = [(k, v) for (k, v) in items if not v.get("cited")]

    limit_n = max(1, int(limit))
    rows: List[Dict[str, Any]] = []
    for key, data in items[:limit_n]:
        used = bool(data.get("cited")) or bool(data.get("used_as_evidence"))
        row: Dict[str, Any] = {
            "paper_id": data.get("paper_id", key),
            "title": (data.get("title", "") or "")[:100],
            "used": used,
        }

        # Include identifiers only when needed
        ck = (data.get("citation_key") or "").strip()
        if ck:
            row["citation_key"] = ck
        else:
            doi = (data.get("doi") or "").strip()
            arxiv_id = (data.get("arxiv_id") or "").strip()
            if doi:
                row["doi"] = doi
            if arxiv_id:
                row["arxiv_id"] = arxiv_id

        y = (data.get("year") or "").strip()
        if y:
            row["year"] = y

        # Only include relevance when it's not the default (3) or when verbose
        rel = int(data.get("relevance", 3) or 3)
        if verbose or rel != 3:
            row["relevance"] = rel

        # Only include these booleans when true, unless verbose
        if verbose or data.get("cited"):
            if data.get("cited"):
                row["cited"] = True
        if verbose or data.get("used_as_evidence"):
            if data.get("used_as_evidence"):
                row["used_as_evidence"] = True

        if verbose:
            src = (data.get("source") or "").strip()
            if src:
                row["source"] = src
            cits = data.get("citations")
            if cits is not None and cits != "":
                row["citations"] = cits

        rows.append(row)

    reviewed_count = len(_reviewed_papers)
    cited_count = sum(1 for v in _reviewed_papers.values() if v.get("cited"))
    evidence_count = sum(1 for v in _reviewed_papers.values() if v.get("used_as_evidence"))
    used_count = sum(1 for v in _reviewed_papers.values() if v.get("cited") or v.get("used_as_evidence"))
    uncited_count = reviewed_count - cited_count
    unused_count = reviewed_count - used_count

    return {
        "summary": {
            "reviewed_count": reviewed_count,
            "cited_count": cited_count,
            "used_as_evidence_count": evidence_count,
            "used_count": used_count,
            "uncited_count": uncited_count,
            "unused_count": unused_count,
            "returned_rows": len(rows),
        },
        "rows": rows,
    }


def fuzzy_cite(query: str) -> List[Dict[str, str]]:
    """
    Fuzzy search for citation keys in the library.
    
    Uses true fuzzy matching (Levenshtein distance) to find papers even with 
    typos or partial queries. Returns citation keys to use as @citation_key.
    
    Args:
        query: Search term - author name, title fragment, year, or keyword.
               Single terms work best (e.g., "vaswani", "attention", "2017").
               Multiple terms use OR logic (matches if ANY term scores well).
    
    Returns:
        List of matching papers with: citation_key, title, authors, year, score
        Track these keys - they will be included in refs.bib
    """
    global _used_citation_keys
    import yaml
    
    # Try to import rapidfuzz for true fuzzy matching
    try:
        from rapidfuzz import fuzz, process
        use_fuzzy = True
    except ImportError:
        use_fuzzy = False
        console.print("[dim]⚠ rapidfuzz not installed, using substring matching[/dim]")
    
    console.print(f"[dim]📚 Fuzzy cite search: {query}[/dim]")
    
    query_lower = query.lower().strip()
    query_parts = query_lower.split()
    
    # Build candidate list from library
    candidates = []
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            
            citation_key = data.get('ref', 'unknown')
            title = data.get('title', 'Unknown')
            authors = data.get('author', 'Unknown')
            year = str(data.get('year', ''))
            
            # Build searchable text
            searchable = f"{citation_key} {title} {authors} {year}".lower()
            
            candidates.append({
                "citation_key": citation_key,
                "title": title,
                "authors": authors,
                "year": year,
                "searchable": searchable,
            })
        except:
            pass
    
    if not candidates:
        console.print(f"[yellow]⚠ Library empty - use discover_papers first[/yellow]")
        return [{
            "citation_key": None,
            "title": "Library is empty",
            "authors": "Use discover_papers to find and add papers first",
            "year": "",
            "suggestion": f"discover_papers(\"{query}\")"
        }]
    
    results = []
    
    if use_fuzzy:
        # True fuzzy matching with rapidfuzz
        # Score each candidate against query parts (OR logic: best match wins)
        scored = []
        for c in candidates:
            # For each query part, compute fuzzy score against searchable text
            # Use partial_ratio for substring-friendly matching
            part_scores = [fuzz.partial_ratio(part, c["searchable"]) for part in query_parts]
            # Also try the full query
            full_score = fuzz.partial_ratio(query_lower, c["searchable"])
            # Best score: max of individual parts or full query
            best_score = max(max(part_scores) if part_scores else 0, full_score)
            
            if best_score >= 60:  # Threshold for "good enough" match
                scored.append((c, best_score))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        
        for c, score in scored[:10]:
            results.append({
                "citation_key": c["citation_key"],
                "title": c["title"][:70],
                "authors": c["authors"][:40],
                "year": c["year"],
                "score": score,
            })
    else:
        # Fallback: substring matching with OR logic (any part matches)
        for c in candidates:
            # OR logic: match if ANY query part is found
            if any(part in c["searchable"] for part in query_parts):
                results.append({
                    "citation_key": c["citation_key"],
                    "title": c["title"][:70],
                    "authors": c["authors"][:40],
                    "year": c["year"],
                })
        results = results[:10]
    
    # Track matched keys
    for r in results:
        key = r["citation_key"]
        if key:
            _used_citation_keys.add(key)
            try:
                track_reviewed_paper(
                    citation_key=key,
                    title=r["title"],
                    authors=r["authors"],
                    year=r["year"],
                    relevance=4,
                    utility=4,
                    source="fuzzy_cite",
                    used_as_evidence=False,
                )
                pid = make_paper_id(citation_key=key)
                if pid in _reviewed_papers:
                    _reviewed_papers[pid]["cited"] = True
            except Exception:
                pass
    
    if not results:
        console.print(f"[yellow]⚠ No matches for '{query}' - use discover_papers first[/yellow]")
        return [{
            "citation_key": None,
            "title": f"No matches for '{query}'",
            "authors": "Use discover_papers() first - papers are auto-added to library",
            "year": "",
            "suggestion": f"discover_papers(\"{query}\")"
        }]
    
    console.print(f"[green]✓ Found {len(results)} matches[/green]")
    return results


def citation_key_to_pdf_filter(citation_key: str) -> str:
    """
    Map a citation key to a PDF filter pattern for query_library.
    
    Because citation keys don't match PDF filenames, this function finds
    the PDF associated with a citation key by looking up the info.yaml.
    
    Args:
        citation_key: The citation key (e.g., "vaswani2017attention")
    
    Returns:
        A filter pattern that will match the PDF (folder name or filename fragment),
        or empty string if not found
    """
    import yaml
    
    key_lower = citation_key.lower().strip()
    
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            
            ref = data.get('ref', '').lower()
            if ref == key_lower:
                # Found it - return the folder name as the filter
                folder_name = info_file.parent.name
                return folder_name
        except:
            pass
    
    # Fallback: return empty string (will query full library)
    return ""



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
    import json
    import ast
    
    # Robust input parsing: handle stringified lists or comma-separated strings
    if isinstance(citation_keys, str):
        original_input = citation_keys
        try:
            # Try JSON first
            if citation_keys.strip().startswith('['):
                 # Try handling single quotes which are invalid in JSON
                 if "'" in citation_keys:
                     citation_keys = ast.literal_eval(citation_keys)
                 else:
                     citation_keys = json.loads(citation_keys)
            else:
                # Comma separated
                citation_keys = [k.strip() for k in citation_keys.split(",")]
        except Exception as e:
            console.print(f"[yellow]⚠ parsing string input for citations: {e}[/yellow]")
            # Fallback cleanup
            citation_keys = [k.strip().strip("'").strip('"') for k in original_input.strip("[]").split(",")]
            
    # Ensure it's a list
    if not isinstance(citation_keys, list):
        citation_keys = [str(citation_keys)]

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
    }
