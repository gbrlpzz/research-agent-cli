"""
Shared research tools module.

This module provides unified tool implementations used by both:
- The autonomous research agent (agent.py)
- The CLI scripts (discover.py, cite.py, etc.)

All tools are importable from this module:
    from tools import discover_papers, add_paper, fuzzy_cite, etc.
"""
from .discovery import discover_papers, exa_search
from .library import add_paper, list_library, query_library
from .citation import (
    fuzzy_cite,
    validate_citations,
    get_used_citation_keys,
    clear_used_citation_keys,
    track_reviewed_paper,
    get_reviewed_papers,
    export_literature_sheet
)

__all__ = [
    # Discovery
    'discover_papers',
    'exa_search',
    # Library management
    'add_paper',
    'list_library', 
    'query_library',
    # Citations
    'fuzzy_cite',
    'validate_citations',
    # Citation tracking
    'get_used_citation_keys',
    'clear_used_citation_keys',
    'track_reviewed_paper',
    'get_reviewed_papers',
    'export_literature_sheet',
]

