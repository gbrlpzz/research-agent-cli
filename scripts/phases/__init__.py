"""
Research phases module.

Contains the structured phases of the research pipeline:
- Planning: create_research_plan, create_argument_map
- Review: peer_review (still in agent.py for now due to dependencies)
"""
from .planner import create_research_plan, create_argument_map

__all__ = [
    'create_research_plan',
    'create_argument_map',
]
