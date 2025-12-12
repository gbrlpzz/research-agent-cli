"""
Tool registry for research agent phases.

Centralizes tool declarations and function dispatch for use by
drafter, reviewer, and reviser phases.
"""
from typing import Any, Callable, Dict, List

# Import tool implementations from shared tools module
from tools import (
    discover_papers,
    exa_search,
    add_paper,
    list_library,
    query_library,
    fuzzy_cite,
    validate_citations,
    get_used_citation_keys,
    clear_used_citation_keys,
    track_reviewed_paper,
    get_reviewed_papers,
    export_literature_sheet,
    literature_sheet,
)


# ============================================================================
# TOOL DECLARATIONS (OpenAI/LiteLLM function-calling)
# ============================================================================

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "discover_papers",
            "description": "Search for papers using Semantic Scholar + paper-scraper, OR traverse citation networks. Use FIRST to find papers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (optional if using cited_by/references)"},
                    "limit": {"type": "integer", "description": "Max results (default 15)"},
                    "cited_by": {"type": "string", "description": "DOI/paper ID for forward citations (papers citing this)"},
                    "references": {"type": "string", "description": "DOI/paper ID for backward citations (papers cited by this)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exa_search",
            "description": "Neural search via Exa.ai. COSTS CREDITS - use only when discover_papers isn't enough.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Conceptual search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_paper",
            "description": "Add a paper to library by arXiv ID or DOI. Downloads PDF and updates master.bib.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "arXiv ID or DOI"},
                    "source": {"type": "string", "description": "'arxiv', 'doi', or 'auto'"},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_library",
            "description": "Ask a research question using PaperQA2 RAG. Uses persistent Qdrant index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Research question"},
                    "paper_filter": {"type": "string", "description": "Optional keyword filter"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fuzzy_cite",
            "description": "Fuzzy search for @citation_keys. Returns keys that ACTUALLY exist in the library.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Author, title, year, or keyword"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_citations",
            "description": "Validate citation keys before writing. Use to ensure all @keys exist in library.",
            "parameters": {
                "type": "object",
                "properties": {
                    "citation_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of citation keys to validate",
                    }
                },
                "required": ["citation_keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_library",
            "description": "List all papers in the library. Check existing papers before adding more.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "literature_sheet",
            "description": "Show the current session's consulted/reviewed literature sheet (what's been added/cited), with summary + top rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max rows to return (default 20)"},
                    "only_uncited": {"type": "boolean", "description": "If true, return only uncited reviewed papers"},
                    "verbose": {"type": "boolean", "description": "If true, include extra fields (source, citations, etc.)"},
                },
            },
        },
    },
]

# Tools that reviewers can use (subset of full tools)
_REVIEWER_ALLOWED_TOOL_NAMES = {
    "query_library",
    "fuzzy_cite",
    "validate_citations",
    "list_library",
    "discover_papers",
    "literature_sheet",
}
REVIEWER_TOOLS: List[Dict[str, Any]] = [
    t for t in TOOLS if t.get("function", {}).get("name") in _REVIEWER_ALLOWED_TOOL_NAMES
]

# Dispatch map: function name -> callable
TOOL_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "discover_papers": discover_papers,
    "exa_search": exa_search,
    "add_paper": add_paper,
    "query_library": query_library,
    "fuzzy_cite": fuzzy_cite,
    "validate_citations": validate_citations,
    "list_library": list_library,
    "literature_sheet": literature_sheet,
}


# Re-export useful functions from tools module
__all__ = [
    "TOOLS",
    "REVIEWER_TOOLS",
    "TOOL_FUNCTIONS",
    "discover_papers",
    "exa_search",
    "add_paper",
    "list_library",
    "query_library",
    "fuzzy_cite",
    "validate_citations",
    "get_used_citation_keys",
    "clear_used_citation_keys",
    "track_reviewed_paper",
    "get_reviewed_papers",
    "export_literature_sheet",
    "literature_sheet",
]
