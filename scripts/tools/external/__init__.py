"""
External tools - not tracked in git.

This directory is for internal/private discovery tools that shouldn't be published.
Add new tools here as needed (e.g., institutional APIs, private databases).

Each consuming module imports with try/except for graceful fallback:

    try:
        from tools.external import fetch_pdf_private, PRIVATE_SOURCES_AVAILABLE
    except ImportError:
        fetch_pdf_private = None
        PRIVATE_SOURCES_AVAILABLE = False
"""

# Private source integration (implementation files are gitignored)
try:
    from .private_sources import fetch_pdf_private, discover_via_private
    PRIVATE_SOURCES_AVAILABLE = True
except ImportError:
    fetch_pdf_private = None
    discover_via_private = None
    PRIVATE_SOURCES_AVAILABLE = False

# Future tools can be added here following the same pattern:
# try:
#     from .institutional_db import search_institutional
#     INSTITUTIONAL_AVAILABLE = True
# except ImportError:
#     search_institutional = None
#     INSTITUTIONAL_AVAILABLE = False
