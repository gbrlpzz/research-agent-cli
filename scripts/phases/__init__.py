"""
Research phases module.

Contains the structured phases of the research pipeline:
- Planning: create_research_plan, create_argument_map
- Drafting: run_agent (initial document generation)
- Review: peer_review (document evaluation)
- Revision: revise_document (incorporate feedback)
- Tools: TOOLS, TOOL_FUNCTIONS, REVIEWER_TOOLS
"""
from .planner import create_research_plan, create_argument_map
from .drafter import run_agent
from .reviewer import peer_review
from .reviser import revise_document
from .tool_registry import (
    TOOLS,
    TOOL_FUNCTIONS,
    REVIEWER_TOOLS,
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

__all__ = [
    # Planning
    'create_research_plan',
    'create_argument_map',
    # Drafting
    'run_agent',
    # Review
    'peer_review',
    # Revision
    'revise_document',
    # Tools
    'TOOLS',
    'TOOL_FUNCTIONS',
    'REVIEWER_TOOLS',
    'discover_papers',
    'exa_search',
    'add_paper',
    'list_library',
    'query_library',
    'fuzzy_cite',
    'validate_citations',
    'get_used_citation_keys',
    'clear_used_citation_keys',
    'track_reviewed_paper',
    'get_reviewed_papers',
    'export_literature_sheet',
    'literature_sheet',
]
