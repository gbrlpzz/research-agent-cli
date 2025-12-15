#!/usr/bin/env python3
"""
Autonomous Research Agent
=========================

An agentic research assistant that autonomously:
1. Plans research by decomposing topics into questions
2. Searches for academic papers (Semantic Scholar + paper-scraper + Exa.ai)
3. Acquires papers by downloading PDFs to the library
4. Synthesizes information using PaperQA2 RAG with persistent Qdrant
5. Generates a Typst document with proper citations

Uses configurable models via LiteLLM (default: Gemini 2.5 Pro for reasoning, Gemini 2.5 Flash for RAG)
to orchestrate the research pipeline. Outputs compiled Typst documents
in the reports/ folder using the project's typst-template.

Usage:
    research agent "Impact of attention mechanisms on NLP"
    
Output:
    reports/<timestamp>_<topic>/
    ├── lib.typ      # Template (from templates/typst-template/)
    ├── refs.bib     # Citations (filtered to cited papers only)
    ├── main.typ     # Generated document
    └── main.pdf     # Compiled PDF
"""

import sys
import os
import json
import subprocess
import shutil
import re
from pathlib import Path
from datetime import datetime
import time
from typing import Optional, List, Dict, Any, Set

# Suppress verbose logging
os.environ['LITELLM_LOG'] = 'ERROR'

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
import logging

from utils.model_config import ModelRouting, apply_routing_to_env, ensure_model_env

# Import from extracted modules
from utils.typst_utils import (
    filter_bibtex_to_cited,
    extract_citations_from_typst,
    fix_typst_error,
    compile_and_fix,
)

# Import all phase modules
from phases.planner import (
    create_research_plan,
    create_argument_map,
    set_model as set_planner_model,
)
from phases.drafter import (
    run_agent,
    set_model as set_drafter_model,
    set_budget as set_drafter_budget,
)
from phases.reviewer import (
    peer_review,
    set_model as set_reviewer_model,
)
from phases.reviser import (
    revise_document,
    set_model as set_reviser_model,
)
from phases.tool_registry import (
    TOOLS,
    TOOL_FUNCTIONS,
    discover_papers,
    add_paper,
    query_library,
    get_used_citation_keys,
    get_reviewed_papers,
    export_literature_sheet,
)
from phases.orchestrator import (
    Orchestrator,
    BudgetMode,
    TaskPhase,
    set_orchestrator,
    get_orchestrator,
)
from utils.telegram_notifier import TelegramNotifier

try:
    import litellm  # type: ignore
except Exception:  # pragma: no cover
    litellm = None

# Setup
console = Console()
load_dotenv()

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = REPO_ROOT / "library"
REPORTS_PATH = REPO_ROOT / "reports"
TEMPLATE_PATH = REPO_ROOT / "templates" / "typst-template"
MASTER_BIB = REPO_ROOT / "master.bib"
PAPIS_CONFIG = REPO_ROOT / "papis.config"

# Models (defaults: Gemini 2.5 Pro for reasoning; Gemini 2.5 Flash for RAG)
_ROUTING = ModelRouting.from_env()
apply_routing_to_env(_ROUTING)
AGENT_MODEL = _ROUTING.reasoning_model

# Configurable iteration limits (can be tuned via .env for complex topics)
MAX_AGENT_ITERATIONS = int(os.getenv('AGENT_MAX_ITERATIONS', '50'))  # Increased from hardcoded 35
MAX_REVISION_ITERATIONS = int(os.getenv('REVISION_MAX_ITERATIONS', '25'))  # For revision phase
MAX_REVIEWER_ITERATIONS = int(os.getenv('MAX_REVIEWER_ITERATIONS', '15'))  # Reviewer iteration limit

# API Safety Timeouts (prevents infinite hangs)
API_TIMEOUT_SECONDS = int(os.getenv('API_TIMEOUT_SECONDS', '120'))  # 2 minutes default
REVIEWER_TIMEOUT_SECONDS = int(os.getenv('REVIEWER_TIMEOUT_SECONDS', '180'))  # 3 minutes for reviewers

# Session timeout (4 hours max to prevent runaway sessions)
MAX_SESSION_DURATION = 4 * 60 * 60  # 4 hours in seconds
_session_start_time: Optional[float] = None

# Global state for tracking which papers were used
_used_citation_keys: Set[str] = set()

# Debug logger (set per session)
_debug_logger: Optional[logging.Logger] = None

# JSON output mode (for external tool integration like WhatsApp bot)
_json_output_mode = False
_telegram_notifier: Optional[TelegramNotifier] = None


def emit_progress(phase: str, status: str = "in_progress", **kwargs):
    """Emit JSON progress update for external tools (e.g., WhatsApp bot).
    
    Only outputs when --json-output flag is set.
    """
    if not _json_output_mode:
        return
    
    update = {
        "phase": phase,
        "status": status,
        **kwargs
    }
    # Print as JSON line (NDJSON format)
    # Print as JSON line (NDJSON format)
    print(json.dumps(update), flush=True)

    # Update Telegram if enabled
    if _telegram_notifier:
        _telegram_notifier.update_status(phase, kwargs)


def setup_debug_log(report_dir: Path) -> logging.Logger:
    """Setup debug logging to a file in the report directory."""
    log_file = report_dir / "artifacts" / "debug.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger(f"agent_{report_dir.name}")
    logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers
    logger.handlers = []
    
    # File handler for debug log
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logger.addHandler(fh)
    
    return logger


def log_debug(msg: str):
    """Log debug message to file only."""
    global _debug_logger
    if _debug_logger:
        _debug_logger.debug(msg)


def validate_checkpoint(checkpoint_file: Path) -> tuple[bool, Optional[str]]:
    """Validate checkpoint file and return (is_valid, error_message)."""
    if not checkpoint_file.exists():
        return False, "Checkpoint file not found"
    
    try:
        checkpoint_data = json.loads(checkpoint_file.read_text())
    except json.JSONDecodeError as e:
        return False, f"Corrupted checkpoint file: {e}"
    except Exception as e:
        return False, f"Failed to read checkpoint: {e}"
    
    # Validate required fields
    if "phase" not in checkpoint_data:
        return False, "Checkpoint missing 'phase' field"
    if "timestamp" not in checkpoint_data:
        return False, "Checkpoint missing 'timestamp' field"
    if "data" not in checkpoint_data:
        return False, "Checkpoint missing 'data' field"
    
    return True, None


def restore_state_from_checkpoint(checkpoint: Dict, report_dir: Path) -> Dict[str, Any]:
    """Restore all necessary state from checkpoint and artifacts."""
    artifacts_dir = report_dir / "artifacts"
    phase = checkpoint["phase"]
    restored = {
        "phase": phase,
        "checkpoint_data": checkpoint["data"],
        "research_plan": None,
        "argument_map": None,
        "typst_content": None,
        "round_reviews_history": [],
        "used_citation_keys": set(),
        "current_revision_round": 0,
    }
    
    # Restore research plan if available
    plan_file = artifacts_dir / "research_plan.json"
    if plan_file.exists():
        try:
            restored["research_plan"] = json.loads(plan_file.read_text())
        except Exception as e:
            console.print(f"[yellow]Warning: Could not restore research plan: {e}[/yellow]")
    
    # Restore argument map if available
    argmap_file = artifacts_dir / "argument_map.json"
    if argmap_file.exists():
        try:
            restored["argument_map"] = json.loads(argmap_file.read_text())
        except Exception as e:
            console.print(f"[yellow]Warning: Could not restore argument map: {e}[/yellow]")
    
    # Restore latest draft content
    # Try to find the most recent draft file
    draft_files = sorted(artifacts_dir.glob("draft_*.typ"))
    if draft_files:
        latest_draft = draft_files[-1]
        try:
            restored["typst_content"] = latest_draft.read_text()
            console.print(f"[dim]Restored draft from: {latest_draft.name}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not restore draft: {e}[/yellow]")
    
    # Restore review history
    review_files = sorted(artifacts_dir.glob("peer_review_r*.json"))
    if review_files:
        # Group by round
        rounds = {}
        for rf in review_files:
            # Extract round number from filename like "peer_review_r1_p1.json"
            match = re.search(r'peer_review_r(\d+)_p\d+\.json', rf.name)
            if match:
                round_num = int(match.group(1))
                if round_num not in rounds:
                    rounds[round_num] = []
                try:
                    review_data = json.loads(rf.read_text())
                    rounds[round_num].append(review_data)
                except Exception:
                    pass
        
        # Convert to list of rounds
        if rounds:
            max_round = max(rounds.keys())
            restored["round_reviews_history"] = [rounds.get(i, []) for i in range(1, max_round + 1)]
            restored["current_revision_round"] = max_round
    
    # Restore citation keys from checkpoint data
    if "citations" in checkpoint["data"]:
        citations = checkpoint["data"]["citations"]
        if isinstance(citations, list):
            restored["used_citation_keys"] = set(citations)
    
    return restored


def set_model_routing(routing: ModelRouting) -> None:
    """Update global model routing and propagate into env vars."""
    global _ROUTING, AGENT_MODEL
    _ROUTING = routing
    apply_routing_to_env(routing)
    AGENT_MODEL = routing.reasoning_model
    # Propagate to all phase modules
    set_planner_model(routing.reasoning_model)
    set_drafter_model(routing.reasoning_model)
    set_reviewer_model(routing.reasoning_model)
    set_reviser_model(routing.reasoning_model)


# ============================================================================
# MULTI-PHASE ORCHESTRATOR
# ============================================================================

def generate_report(topic: str, max_revisions: int = 3, num_reviewers: int = 1, resume_from: Optional[Path] = None) -> Path:
    """Generate a research report with planning, review, and revision phases.
    
    Args:
        topic: Research topic
        max_revisions: Maximum number of revision rounds
        num_reviewers: Number of parallel reviewers
        resume_from: Optional path to existing report directory to resume from
    """
    global _used_citation_keys, _debug_logger, _session_start_time, _telegram_notifier
    _used_citation_keys = set()
    _session_start_time = time.time()  # Track session start for timeout

    # Initialize Telegram Notifier
    # Check for CLI args override first (conceptually - though we access global args via sys.argv or env)
    # Ideally passed via function args, but for now we trust env + args parsing wrapper
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("AUTHORIZED_USER_ID")
    
    # Simple CLI arg parsing for chat-id override (since we don't want to change main() signature too much yet)
    if "--telegram-chat-id" in sys.argv:
        try:
            idx = sys.argv.index("--telegram-chat-id")
            if idx + 1 < len(sys.argv):
                chat_id = sys.argv[idx + 1]
        except:
            pass

    if chat_id:
        _telegram_notifier = TelegramNotifier(chat_id=chat_id)
        # Send start message if it's a new run
        if not resume_from:
             _telegram_notifier.start_research(topic, AGENT_MODEL)
    
    
    def check_session_timeout():
        """Check if session has exceeded max duration."""
        if _session_start_time:
            elapsed = time.time() - _session_start_time
            if elapsed > MAX_SESSION_DURATION:
                hours = MAX_SESSION_DURATION / 3600
                log_debug(f"Session timeout after {elapsed/3600:.1f} hours (max: {hours} hours)")
                console.print(f"[yellow]⚠ Session timeout ({hours} hours max). Saving current progress.[/yellow]")
                return True
        return False
    
    REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    
    # Resume logic: use existing directory or create new one
    resumed_state = None
    if resume_from:
        # Resuming from existing report
        report_dir = resume_from.resolve()
        if not report_dir.exists():
            raise RuntimeError(f"Resume directory does not exist: {report_dir}")
        
        checkpoint_file = report_dir / "artifacts" / "checkpoint.json"
        is_valid, error_msg = validate_checkpoint(checkpoint_file)
        if not is_valid:
            raise RuntimeError(f"Cannot resume: {error_msg}")
        
        # Load and restore state
        checkpoint = json.loads(checkpoint_file.read_text())
        resumed_state = restore_state_from_checkpoint(checkpoint, report_dir)
        
        console.print(Panel(
            f"[bold cyan]📂 Resuming from Checkpoint[/bold cyan]\n\n"
            f"[white]Phase:[/white] {resumed_state['phase']}\n"
            f"[white]Time:[/white] {checkpoint.get('timestamp', 'unknown')}\n"
            f"[white]Round:[/white] {resumed_state['current_revision_round']}\n\n"
            f"[dim]Will skip completed phases and resume from next phase.[/dim]",
            title="Resume Mode",
            border_style="cyan"
        ))
        
        # Restore global state
        _used_citation_keys = resumed_state["used_citation_keys"]
        
    else:
        # Create new report directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        topic_slug = "".join(c if c.isalnum() or c == " " else "" for c in topic[:40])
        topic_slug = topic_slug.strip().replace(" ", "_").lower()
        report_name = f"{timestamp}_{topic_slug}"
        report_dir = REPORTS_PATH / report_name
        report_dir.mkdir(parents=True, exist_ok=True)
    
    # Checkpoint system for crash recovery
    checkpoint_file = report_dir / "artifacts" / "checkpoint.json"
    checkpoint_file.parent.mkdir(exist_ok=True)
    
    def save_checkpoint(phase: str, data: Dict[str, Any]):
        """Save progress checkpoint for crash recovery."""
        checkpoint = {
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
        try:
            checkpoint_file.write_text(json.dumps(checkpoint, indent=2, default=str))
            console.print(f"[dim]💾 Checkpoint saved: {phase}[/dim]")
        except Exception as e:
            console.print(f"[dim yellow]⚠ Checkpoint save failed: {e}[/dim yellow]")
    
    def load_checkpoint() -> Optional[Dict]:
        """Load existing checkpoint if present."""
        if checkpoint_file.exists():
            try:
                return json.loads(checkpoint_file.read_text())
            except Exception:
                return None
        return None
    
    # Create artifacts subfolder
    artifacts_dir = report_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    # Import UI
    from utils.ui import UIManager, set_ui, get_ui

    # Setup debug logging
    _debug_logger = setup_debug_log(report_dir)
    log_debug(f"Starting research session: {topic}")
    
    # Initialize UI
    ui = UIManager(topic=topic, model_name=AGENT_MODEL)
    set_ui(ui)
    ui.start()
    
    try:
        log_debug(f"Max revisions: {max_revisions}")
        if resumed_state:
            log_debug(f"Resuming from phase: {resumed_state['phase']}")
            ui.log(f"Resuming from checkpoint ({resumed_state['phase']})", "WARNING")
        
        # Initialize variables that may be restored or created fresh
        research_plan = None
        argument_map = None
        typst_content = None
        round_reviews_history = []
        
        # ========== PHASE 1: PLANNING ==========
        if not resumed_state or resumed_state['phase'] in ['research_plan']:
            # Only run if not resuming or if we need to redo planning
            orch = get_orchestrator()
            model = orch.start_phase(TaskPhase.PLANNING)
            
            # Optimization: Use Flash for Planning if on Gemini Pro to save quota
            if model.startswith("gemini/") and "pro" in model and "flash" not in model:
                 model = "gemini/gemini-2.5-flash" 
                 ui.log("Optimization: Switched Planning to Gemini 2.5 Flash", "DEBUG")
            
            # Optimization: Use Sonnet for Planning if on Antigravity
            if model.startswith("antigravity/"):
                 model = "antigravity/claude-3-5-sonnet"
                 ui.log("Optimization: Using Claude 3.5 Sonnet for Planning", "DEBUG")
                 
            set_planner_model(model)
            
            emit_progress("Planning", "in_progress")
            
            ui.set_phase("Planning", model)
            ui.set_status("Decomposing research topic...")
            
            research_plan = create_research_plan(topic)
            save_checkpoint("research_plan", {"plan": research_plan, "library_size": len(list(LIBRARY_PATH.rglob("*.pdf")))})
            # Save plan to artifacts
            (artifacts_dir / "research_plan.json").write_text(json.dumps(research_plan, indent=2))
            emit_progress("Planning", "complete", questions=len(research_plan.get("sub_questions", [])))
            
            ui.log(f"Plan created with {len(research_plan.get('sub_questions', []))} sub-questions", "SUCCESS")
            log_debug(f"Research plan created: {json.dumps(research_plan)}")
        else:
            # Skip and restore from checkpoint
            ui.log("Skipping Phase 1 (Planning) - already completed", "DEBUG")
            research_plan = resumed_state['research_plan']
            log_debug("Research plan restored from checkpoint")
    finally:
        ui.stop()
    
    # ========== PHASE 1b: ARGUMENT DISSECTION ==========
        if not resumed_state or resumed_state['phase'] in ['research_plan', 'argument_map']:
            orch = get_orchestrator()
            model = orch.start_phase(TaskPhase.ARGUMENT_MAP)
            set_planner_model(model)
            
            emit_progress("ArgumentMap", "in_progress")
            
            ui.set_phase("Argument Dissection", model)
            ui.set_status("Analyzing arguments...")
            
            argument_map = create_argument_map(topic, research_plan)
            save_checkpoint("argument_map", {"map": argument_map})
            (artifacts_dir / "argument_map.json").write_text(json.dumps(argument_map, indent=2))
            emit_progress("ArgumentMap", "complete")
            
            ui.log("Argument map created", "SUCCESS")
            log_debug(f"Argument map created: {json.dumps(argument_map)}")
        else:
            ui.log("Skipping Phase 1b (Argument Dissection) - already completed", "DEBUG")
            argument_map = resumed_state['argument_map']
            log_debug("Argument map restored from checkpoint")
        
        # ========== PHASE 2: RESEARCH & WRITE ==========
        # Check for incomplete draft (drafter_state.json exists = draft in progress)
        drafter_state_file = artifacts_dir / "drafter_state.json"
        draft_incomplete = drafter_state_file.exists()
        
        if not resumed_state or resumed_state['phase'] in ['research_plan', 'argument_map', 'initial_draft'] or draft_incomplete:
            orch = get_orchestrator()
            model = orch.start_phase(TaskPhase.DRAFTING)
            set_drafter_model(model)
            set_drafter_budget(orch.budget_mode.value)
            
            emit_progress("Drafting", "in_progress")
            
            ui.set_phase("Drafting", model)
            ui.set_status("Researching and writing draft...")
            
            # State file for granular step-by-step resume
            
            try:
                typst_content = run_agent(topic, research_plan=research_plan, argument_map=argument_map, state_file=drafter_state_file)
            except RuntimeError as e:
                # Track error for potential escalation
                orch.record_error(TaskPhase.DRAFTING)
                # Fail fast on model access issues; don't continue generating empty drafts/reviews.
                ui.log(f"Fatal LLM error: {e}", "ERROR")
                raise
    
            if not typst_content or typst_content.strip().startswith("// Agent"):
                # Catches both "// Agent did not produce" and "// Agent failed - state saved"
                raise RuntimeError("Agent failed to produce a draft. Aborting to avoid generating empty reports.")
        else:
            ui.log("Skipping Phase 2 (Research & Writing) - already completed", "DEBUG")
            typst_content = resumed_state['typst_content']
            round_reviews_history = resumed_state['round_reviews_history']
            log_debug("Draft content and review history restored from checkpoint")
        
        # Save drafts and generate refs.bib
        save_checkpoint("initial_draft", {"document": typst_content, "citations": list(_used_citation_keys)})
        (artifacts_dir / "draft_initial.typ").write_text(typst_content)
        
        # Generate refs.bib for the initial draft
        doc_citations = extract_citations_from_typst(typst_content)
        all_cited = get_used_citation_keys() | doc_citations
        if MASTER_BIB.exists():
            current_refs_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
        else:
            current_refs_bib = "% No references\n"
        (artifacts_dir / "draft_initial_refs.bib").write_text(current_refs_bib)
        
        # Copy lib.typ and refs.bib to artifacts for standalone draft compilation
        if (TEMPLATE_PATH / "lib.typ").exists():
            shutil.copy(TEMPLATE_PATH / "lib.typ", artifacts_dir / "lib.typ")
        (artifacts_dir / "refs.bib").write_text(current_refs_bib)
        
        # Compile initial draft to PDF (with self-fixing)
        try:
            ui.set_status("Compiling initial draft...")
            compile_and_fix(artifacts_dir / "draft_initial.typ")
            ui.log("Initial draft compiled", "SUCCESS")
        except Exception:
            pass  # Don't fail if typst not available
        
        emit_progress("Drafting", "complete", citations=len(all_cited))
        ui.update_metrics(cost=0.0, tokens=0, citations=len(all_cited)) # Cost updated via orchestrator later
        log_debug(f"Initial draft complete with {len(all_cited)} citations")
        
        # ========== PHASE 3: PEER REVIEW LOOP ==========
        reviews = []
        # round_reviews_history already initialized above (may be restored from checkpoint)
        
        # Determine starting round for revision loop
        start_round = 1
        if resumed_state and resumed_state['current_revision_round'] > 0:
            # Resume from next round after the last completed one
            start_round = resumed_state['current_revision_round'] + 1
            ui.log(f"Resuming from revision round {start_round}", "INFO")
        
        for revision_round in range(start_round, max_revisions + 1):
            # Check session timeout at start of each revision round
            if check_session_timeout():
                ui.log("Skipping further revisions due to session timeout", "WARNING")
                break
                
            # Set up orchestrator for review phase
            orch = get_orchestrator()
            model = orch.start_phase(TaskPhase.REVIEW)
            set_reviewer_model(model)
            
            emit_progress("Review", "in_progress", round=revision_round)
            
            ui.set_phase(f"Review (Round {revision_round})", model)
            ui.set_status(f"Gathering feedback from {num_reviewers} reviewers...")
            
            # Generate refs.bib for the current draft
            doc_citations = extract_citations_from_typst(typst_content)
            all_cited = get_used_citation_keys() | doc_citations
            if MASTER_BIB.exists():
                current_refs_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
            else:
                current_refs_bib = "% No references\n"
            
            # Peer review with full context
            round_reviews = []
            verdicts = []
            
            # Prepare previous reviews context for this round
            previous_reviews_text = ""
            if round_reviews_history:
                for rnd, reviews in enumerate(round_reviews_history, 1):
                    previous_reviews_text += f"\n--- ROUND {rnd} FEEDBACK ---\n"
                    for i, r_data in enumerate(reviews, 1):
                        previous_reviews_text += f"Reviewer {i}: {r_data.get('summary')}\n"
                        if r_data.get('weaknesses'):
                            previous_reviews_text += f"Weaknesses: {r_data.get('weaknesses')}\n"
                        if r_data.get('missing_citations'):
                             previous_reviews_text += f"Missing Citations: {r_data.get('missing_citations')}\n"
    
            for r_idx in range(1, num_reviewers + 1):
                ui.set_status(f"Reviewer {r_idx} analyzing document...")
                review_result = peer_review(
                    typst_content,
                    topic,
                    revision_round,
                    reviewer_id=r_idx,
                    research_plan=research_plan,
                    refs_bib=current_refs_bib,
                    previous_reviews=previous_reviews_text
                )
                round_reviews.append(review_result)
                verdicts.append(review_result.get('verdict', 'minor_revisions'))
                
                # Save individual review
                (artifacts_dir / f"peer_review_r{revision_round}_p{r_idx}.json").write_text(json.dumps(review_result, indent=2))
            
            # Track history
            round_reviews_history.append(round_reviews)
        
            # Aggregate reviews for revision
            # Check if ALL accepted
            if all(v == 'accept' for v in verdicts):
                ui.log("Paper accepted by all reviewers!", "SUCCESS")
                break
                
            # Process Recommended Papers from Reviewers
            all_recommendations = []
            for rr in round_reviews:
                if "recommended_papers" in rr:
                    all_recommendations.extend(rr["recommended_papers"])
            
            added_citations = []
            if all_recommendations:
                ui.log(f"Processing {len(all_recommendations)} Reviewer Recommendations", "INFO")
                for rec in all_recommendations:
                    # Handle DOI
                    if "doi" in rec:
                        try:
                            ui.log(f"Adding recommended DOI: {rec['doi']}", "INFO")
                            add_paper(identifier=rec['doi'], source="doi")
                            added_citations.append(f"DOI: {rec['doi']} (Reason: {rec.get('reason', 'Reviewer recommended')})")
                        except Exception as e:
                            ui.log(f"Failed to add DOI {rec['doi']}: {e}", "ERROR")
                    # Handle Query
                    elif "query" in rec:
                        try:
                            ui.set_status(f"Discovering: {rec['query']}")
                            # First discover
                            found = discover_papers(query=rec['query'], limit=3)
                            # Then auto-add top 1 if found
                            if found and found[0].get("title"): # Check if valid result
                                 first_paper = found[0]
                                 ident = first_paper.get("doi") or first_paper.get("arxivId") or first_paper.get("arxiv_id")
                                 if ident:
                                     ui.log(f"Adding discovered paper: {ident}", "INFO")
                                     add_paper(identifier=ident, source="doi" if first_paper.get("doi") else "arxiv")
                                     added_citations.append(f"Discovered: {first_paper.get('title')} ({ident})")
                        except Exception as e:
                            ui.log(f"Failed to process query {rec['query']}: {e}", "ERROR")

            # Synthesize feedback string
            combined_feedback = f"Processing {len(round_reviews)} reviews.\n\n"
            
            if added_citations:
                 ui.set_status("Indexing new papers and generating summaries...")
                 
                 # Force update index (by calling query_library widely)
                 paper_summaries = []
                 try:
                     for ac_text in added_citations:
                         # ac_text format: "Discovered: Title (ID)" or "DOI: ID (Reason)"
                         if "Discovered:" in ac_text:
                             title_part = ac_text.split("Discovered:")[1].split("(")[0].strip()
                             query_text = f"What are the key findings of the paper '{title_part}'?"
                         else:
                             doi_part = ac_text.split("DOI:")[1].split("(")[0].strip()
                             query_text = f"What are the key findings of the paper with DOI {doi_part}?"
                         
                         # Check if we should query
                         ui.set_status(f"Summarizing: {query_text[:30]}...")
                         answer = query_library(query_text) # This triggers indexing!
                         if answer and "I cannot answer" not in answer:
                             paper_summaries.append(f"**Paper**: {ac_text}\n**Summary**: {answer}\n")
                 except Exception as e:
                     ui.log(f"Failed to summarize new papers: {e}", "WARNING")

                 combined_feedback += "## 📚 PRE-REVISION LITERATURE UPDATE\n"
                 combined_feedback += "The following papers suggested by reviewers have been AUTOMATICALLY ADDED to the library. YOU MUST REVIEW AND INTEGRATE THEM:\n"
                 for ac in added_citations:
                     combined_feedback += f"- {ac}\n"
                 
                 if paper_summaries:
                     combined_feedback += "\n### 📝 ABSTRACTS / SUMMARIES OF NEW PAPERS\n"
                     combined_feedback += "\n".join(paper_summaries)
                     combined_feedback += "\n[End of New Literature]\n"
                 combined_feedback += "\n"

            # Display Review Summaries to User
            ui.log(f"Review Round {revision_round} Summaries:", "INFO")
            
            # Calculate aggregated verdict
            verdicts = [rr.get('verdict', 'unknown') for rr in round_reviews]
            if 'major_revisions' in verdicts:
                aggregated_verdict = 'major_revisions'
            elif 'minor_revisions' in verdicts:
                aggregated_verdict = 'minor_revisions'
            elif 'accept' in verdicts:
                aggregated_verdict = 'accept'
            else:
                aggregated_verdict = 'unknown'
            
            for i, rr in enumerate(round_reviews, 1):
                verdict = rr.get('verdict', 'unknown')
                ui.log(f"Reviewer {i}: {verdict.upper()}", "SUCCESS" if verdict == 'accept' else "WARNING")
                
                # Add simple summary log
                summary = rr.get('summary', '')[:100] + "..." if len(rr.get('summary', '')) > 100 else rr.get('summary', '')
                ui.log(f"  {summary}", "DEBUG")

                combined_feedback += f"--- REVIEWER {i} ({rr.get('verdict')}) ---\n"
                combined_feedback += f"Summary: {rr.get('summary')}\n"
                combined_feedback += f"Weaknesses: {rr.get('weaknesses')}\n"
                combined_feedback += f"Hallucinations: {rr.get('hallucinations')}\n"
                combined_feedback += f"Specific Edits: {rr.get('specific_edits')}\n\n"
                
            # Save aggregated feedback
            (artifacts_dir / f"aggregated_feedback_r{revision_round}.txt").write_text(combined_feedback)
            
            # Checkpoint after review round
            save_checkpoint(f"peer_review_r{revision_round}", {
                "round": revision_round,
                "reviews": round_reviews,
                "verdict": aggregated_verdict,
                "feedback": combined_feedback
            })
            
            # Determine if revision is needed based on verdict
            needs_revision = aggregated_verdict in ['major_revisions', 'minor_revisions']
            
            log_debug(f"Round {revision_round} verdict: {aggregated_verdict}, needs_revision: {needs_revision}")
            
            if not needs_revision:
                break # Exit loop if accepted
                
            # ========== PHASE 4: REVISION ==========
            orch = get_orchestrator()
            model = orch.start_phase(TaskPhase.REVISION)
            set_reviser_model(model)
            
            emit_progress("Revision", "in_progress", round=revision_round)
            
            ui.set_phase(f"Revision (Round {revision_round})", model)
            ui.set_status("Revising document based on feedback...")
            
            typst_content = revise_document(
                typst_content, 
                combined_feedback, 
                topic,
                research_plan
            )
            emit_progress("Revision", "complete", round=revision_round)
            
            # Save draft with its refs.bib to artifacts (complete, reviewable document)
            draft_file = artifacts_dir / f"draft_r{revision_round}.typ"
            draft_file.write_text(typst_content)
            
            # Generate refs.bib for this draft
            doc_citations = extract_citations_from_typst(typst_content)
            all_cited = get_used_citation_keys() | doc_citations
            if MASTER_BIB.exists():
                draft_refs_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
            else:
                draft_refs_bib = "% No references\n"
            (artifacts_dir / f"draft_r{revision_round}_refs.bib").write_text(draft_refs_bib)
            
            # Update shared refs.bib and compile revision draft to PDF
            (artifacts_dir / "refs.bib").write_text(draft_refs_bib)
            try:
                ui.set_status("Compiling revision...")
                compile_and_fix(artifacts_dir / f"draft_r{revision_round}.typ")
                ui.log(f"Revision {revision_round} compiled", "SUCCESS")
            except Exception:
                pass  # Don't fail if typst not available
            
            # Checkpoint after revision
            save_checkpoint(f"revision_r{revision_round}", {
                "round": revision_round,
                "document": typst_content,
                "citations": list(_used_citation_keys)
            })
            
            ui.update_metrics(cost=0.0, tokens=0, citations=len(all_cited))
            log_debug(f"Revision {revision_round} complete with {len(all_cited)} citations")
    
    # ========== FINAL OUTPUT ==========
    ui.set_phase("Finalization")
    ui.set_status("Generating final PDF report...")
    
    # Copy template
    if (TEMPLATE_PATH / "lib.typ").exists():
        shutil.copy(TEMPLATE_PATH / "lib.typ", report_dir / "lib.typ")
    
    # Extract and filter citations
    doc_citations = extract_citations_from_typst(typst_content)
    all_cited = _used_citation_keys | doc_citations
    log_debug(f"Cited keys: {all_cited}")
    
    # Create filtered refs.bib
    if MASTER_BIB.exists():
        filtered_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
        (report_dir / "refs.bib").write_text(filtered_bib)
    else:
        (report_dir / "refs.bib").write_text("% No references\n")
    
    # Get cost summary for document injection
    orch = get_orchestrator()
    cost_summary = orch.get_summary()
    
    # Format cost info as Typst content with line break
    # Example: [150,000 Tokens \ $0.00]
    tokens_str = f"{cost_summary['total_tokens']:,} Tokens"
    cost_str = f"{cost_summary['total_cost']}"
    cost_info_val = f"[{tokens_str} \\\\ {cost_str}]"
    
    # Inject star_hash parameter into the document
    if 'star_hash:' not in typst_content:
        typst_content = typst_content.replace(
            'date:',
            'star_hash: "star_hash.svg",\n  date:'
        )
    
    # Inject cost_info parameter into the document
    if 'cost_info:' not in typst_content:
        typst_content = typst_content.replace(
            'star_hash:',
            f'cost_info: {cost_info_val},\n  star_hash:'
        )
    
    # Write final main.typ
    main_typ = report_dir / "main.typ"
    main_typ.write_text(typst_content)
    
    # Copy compile.sh and compile using it (generates star hash + PDF)
    compile_script = TEMPLATE_PATH / "compile.sh"
    if compile_script.exists():
        shutil.copy(compile_script, report_dir / "compile.sh")
        (report_dir / "compile.sh").chmod(0o755)
    
    # Try to compile with retries and auto-fixing
    max_retries = 3
    pdf_generated = False
    
    for attempt in range(max_retries):
        try:
            ui.set_status(f"Compiling PDF (Attempt {attempt+1}/{max_retries})...")
            # Use compile.sh for unified star hash + typst compile
            if (report_dir / "compile.sh").exists():
                result = subprocess.run(
                    ["./compile.sh"],
                    cwd=report_dir,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
            else:
                # Fallback to plain typst
                result = subprocess.run(
                    ["typst", "compile", "main.typ"],
                    cwd=report_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
            
            if result.returncode == 0:
                pdf_generated = True
                break
            else:
                log_debug(f"Compile error (attempt {attempt+1}): {result.stderr}")
                ui.log(f"Compile error: {result.stderr[:100]}...", "WARNING")
                
                # Attempt Auto-Fix
                if attempt < max_retries - 1:
                    ui.log("Attempting to auto-fix Typst syntax...", "INFO")
                    from utils.typst_utils import fix_typst_error
                    if fix_typst_error(main_typ, result.stderr):
                        ui.log("Applied auto-fix, retrying...", "SUCCESS")
                        continue
                    else:
                        ui.log("No fix available.", "WARNING")
        
        except Exception as e:
            log_debug(f"Compile exception: {e}")
    
    if not pdf_generated:
        ui.log("Failed to compile PDF after retries", "ERROR")

    final_pdf = report_dir / "main.pdf"
    if final_pdf.exists():
        ui.log("Report generated successfully", "SUCCESS")
        # Use urgent=True to show modal dialog that breaks through
        ui.send_notification(
            f"Research on '{topic}' is complete!\nPDF ready at: {final_pdf.name}", 
            "Research Agent Success",
            urgent=True,
            reveal_path=str(final_pdf)
        )
        if _telegram_notifier:
            try:
                # Extract title for caption
                caption = f"📄 {topic[:50]}"
                with open(main_typ, 'r') as f:
                    for line in f:
                        if line.strip().startswith('title:'):
                            caption = line.split(':', 1)[1].strip().strip('"').strip(',')
                            break
                _telegram_notifier.send_document(final_pdf, caption=caption)
            except Exception as e:
                ui.log(f"Failed to send PDF to Telegram: {e}", "WARNING")
    else:
        if _telegram_notifier:
            _telegram_notifier.send_message(f"❌ Research complete but PDF generation failed. Check logs.")
    
    # Export literature sheet
    literature_sheet = export_literature_sheet()
    (report_dir / "literature_sheet.csv").write_text(literature_sheet)
    # Markdown sheet removed as requested
    log_debug(f"Literature sheet exported with {len(get_reviewed_papers())} papers")
    
    # Get cost summary from orchestrator
    orch = get_orchestrator()
    cost_summary = orch.get_summary()
    
    # Save cost report to artifacts
    (artifacts_dir / "cost_report.json").write_text(json.dumps(cost_summary, indent=2))
    log_debug(f"Cost report: {cost_summary['total_cost']} for {cost_summary['total_tokens']:,} tokens")
    
    # Summary
    reviewed_count = len(get_reviewed_papers())
    # Robust citation count from used keys directly
    cited_count = len(get_used_citation_keys())
    
    # STOP UI HERE TO RESTORE CONSOLE FOR FINAL SUMMARY
    ui.stop()
    
    console.print("\n" + "="*60)
    console.print(Panel(
        f"[bold green]✓ Research Complete[/bold green]\n\n"
        f"[white]Topic:[/white] {topic[:50]}...\n"
        f"[white]Reviews:[/white] {len(reviews)} round{'s' if len(reviews) != 1 else ''}\n"
        f"[white]Final verdict:[/white] {reviews[-1]['verdict'].upper() if reviews else 'N/A'}\n"
        f"[white]Papers:[/white] {cited_count} cited / {reviewed_count} reviewed\n"
        f"[white]Cost:[/white] {cost_summary['total_cost']} ({cost_summary['total_tokens']:,} tokens)\n\n"
        f"[dim]Output:[/dim]\n"
        f"  📝 main.typ\n"
        f"  📄 main.pdf\n"
        f"  📚 refs.bib\n"
        f"  📊 literature_sheet.csv\n"
        f"  💰 artifacts/cost_report.json\n"
        f"  📁 artifacts/ (plans, drafts, reviews)\n\n"
        f"[dim]{report_dir}[/dim]",
        border_style="green"
    ))
    
    # Print detailed cost breakdown
    orch.print_summary()
    
    log_debug(f"Session complete: {report_dir}")
    
    # Emit final completion with PDF path for external tools
    pdf_path = report_dir / "main.pdf"
    emit_progress("Complete", "complete", pdf_path=str(pdf_path), report_dir=str(report_dir), cost=cost_summary['total_cost'])
    
    return report_dir


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def interactive_config(topic: str) -> dict:
    """Show interactive config menu before research starts."""
    from rich.prompt import Prompt, IntPrompt
    
    if not topic:
        topic = Prompt.ask("[bold cyan]Enter research topic[/bold cyan]")
        if not topic:
            sys.exit(0)

    console.print()
    console.print(Panel(
        f"[bold cyan]Research Agent Config[/bold cyan]\n\n"
        f"[white]Topic:[/white] {topic[:60]}{'...' if len(topic) > 60 else ''}",
        border_style="cyan"
    ))
    console.print()
    
    console.print("[bold cyan]Select Research Profile:[/bold cyan]")
    console.print("1. [bold]Gemini Plan[/bold]      (High budget, Gemini 3 Pro via OAuth)")
    console.print("2. [bold]Antigravity Plan[/bold] (High budget, Claude Opus 4.5 Thinking)")
    console.print("3. [bold]API Mode[/bold]         (Low budget, Gemini 2.5 Flash via API Key)")
    
    profile = Prompt.ask(
        "Choose profile",
        choices=["1", "2", "3"],
        default="1"
    )
    
    from utils.llm import set_oauth_enabled
    
    if profile == "1":
        reasoning_model = "gemini/gemini-3-pro-preview"
        budget = "high"
        set_oauth_enabled(True)
    elif profile == "2":
        set_oauth_enabled(True)
        budget = "high"
        console.print("\n[bold]Select Antigravity Model:[/bold]")
        console.print("1. Claude Opus 4.5 Thinking (Default)")
        console.print("2. Claude 3.5 Sonnet")
        console.print("3. Claude 3 Opus")
        console.print("4. Gemini 3 Pro")
        
        ag_choice = Prompt.ask("Choose model", choices=["1", "2", "3", "4"], default="1")
        if ag_choice == "1":
            reasoning_model = "antigravity/claude-opus-4-5-thinking"
        elif ag_choice == "2":
            reasoning_model = "antigravity/claude-3-5-sonnet"
        elif ag_choice == "3":
            reasoning_model = "antigravity/claude-3-opus"
        else:
            reasoning_model = "antigravity/gemini-3-pro"
    else:
        reasoning_model = "gemini/gemini-2.5-flash"
        budget = "low"
        set_oauth_enabled(False)
    
    console.print(f"[dim]Selected: {reasoning_model} ({budget})[/dim]\n")

    # Iterations and revisions
    max_iterations = IntPrompt.ask("[cyan]Max agent iterations[/cyan]", default=50 if budget == "high" else 30)
    revisions = IntPrompt.ask("[cyan]Revision rounds[/cyan]", default=3 if budget == "high" else 1)
    
    console.print()
    return {
        "reasoning_model": reasoning_model,
        "max_iterations": max_iterations,
        "revisions": revisions,
        "budget": budget,
        "topic": topic,
    }


if __name__ == "__main__":
    import argparse
    import sys
    
    # Manual command routing to avoid conflict with 'topic' argument (which consumes everything)
    # This ensures 'research gemini-login' works even though 'topic' has nargs='*'
    command_mode = None
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd.startswith("gemini-") or cmd.startswith("antigravity-"):
            command_mode = cmd
    
    parser = argparse.ArgumentParser(
        description="Autonomous Research Agent with Peer Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  research "Impact of attention mechanisms on NLP"
  research -r 5 "Vision Transformers vs CNNs"
  
Gemini OAuth (use your Gemini plan quota):
  research gemini-login     # Authenticate with Google OAuth
  research gemini-logout    # Clear stored OAuth tokens
  research gemini-status    # Check OAuth status

Antigravity (Claude Opus 4.5 Thinking):
  research antigravity-login
  research antigravity-logout
  research antigravity-status
        """
    )
    
    # We only add subparsers IF we detected a command mode, OR we don't add them at all
    # and Handle manually. Mixing subparsers and nargs='*' is tricky in argparse.
    # Strategy: If a command is present, use a parser that expects it. If not, use the research parser.
    
    if command_mode == 'gemini-login':
        parser.add_argument('command', choices=['gemini-login'])
        parser.add_argument('--project-id', type=str, default='',
                          help='Google Cloud project ID (will prompt if not provided)')
    
    elif command_mode == 'gemini-logout':
        parser.add_argument('command', choices=['gemini-logout'])
        
    elif command_mode == 'gemini-status':
        parser.add_argument('command', choices=['gemini-status'])
        
    elif command_mode == 'antigravity-login':
        parser.add_argument('command', choices=['antigravity-login'])
        parser.add_argument('--project-id', type=str, default='', help='Google Cloud Project ID')

    elif command_mode == 'antigravity-logout':
        parser.add_argument('command', choices=['antigravity-logout'])

    elif command_mode == 'antigravity-status':
        parser.add_argument('command', choices=['antigravity-status'])
        
    else:
        # Standard research mode
        parser.add_argument('topic', nargs='*', help='Research topic (optional if resuming)')
        parser.add_argument('--interactive', '-i', action='store_true',
                           help='Show config menu before starting')
        parser.add_argument('--resume', type=str, default=None,
                           help='Resume from existing report directory (e.g., reports/20251212_150513_...)')
        parser.add_argument('--revisions', '-r', type=int, default=3,
                           help='Max peer review rounds (default: 3)')
        parser.add_argument('--reviewers', type=int, default=1,
                           help='Number of parallel reviewers (default: 1)')
        parser.add_argument(
            "--reasoning-model",
            default=None,
            help="Model for reasoning/planning/writing/reviewing (default: GPT-5.2 High)",
        )
        parser.add_argument(
            "--rag-model",
            default=None,
            help="Model for PaperQA RAG over your library (default: GPT-5.2 Fast)",
        )
        parser.add_argument(
            "--embedding-model",
            default=None,
            help="Embedding model for PaperQA indexing (default: text-embedding-3-large)",
        )
        parser.add_argument(
            "--json-output",
            action="store_true",
            help="Output JSON progress updates for external tool integration (e.g., WhatsApp bot)",
        )
        parser.add_argument(
            "--telegram-chat-id",
            default=None,
            help="Telegram chat ID for sending progress updates (used by Telegram bot)",
        )
        parser.add_argument(
        "--budget",
        default=None,
        choices=["low", "balanced", "high"],
        help="Budget mode: low (cost-saving), balanced, or high (quality). Default: low (or high if using Gemini OAuth)",
    )
    
    args = parser.parse_args()
    
    # Handle Gemini OAuth commands
    if command_mode == 'gemini-login':
        from utils.gemini_oauth import interactive_login
        success = interactive_login(args.project_id)
        sys.exit(0 if success else 1)
    
    if command_mode == 'gemini-logout':
        from utils.gemini_oauth import logout
        logout()
        sys.exit(0)
    
    if command_mode == 'gemini-status':
        from utils.gemini_oauth import load_tokens, is_oauth_available
        if is_oauth_available():
            tokens = load_tokens()
            console.print("[green]✓ Gemini OAuth is configured[/green]")
            if tokens:
                console.print(f"  [dim]Email: {tokens.email or 'unknown'}[/dim]")
                console.print(f"  [dim]Project: {tokens.project_id}[/dim]")
                if tokens.is_expired():
                    console.print("  [yellow]Token expired (will auto-refresh on next use)[/yellow]")
                else:
                    console.print("  [dim]Token valid[/dim]")
        else:
            console.print("[yellow]✗ Gemini OAuth not configured[/yellow]")
            console.print("  [dim]Run 'research gemini-login' to authenticate[/dim]")
        sys.exit(0)

    # Handle Antigravity commands
    if command_mode == 'antigravity-login':
        from utils.antigravity_oauth import interactive_login
        success = interactive_login(args.project_id)
        sys.exit(0 if success else 1)
        
    if command_mode == 'antigravity-logout':
        from utils.antigravity_oauth import logout
        logout()
        sys.exit(0)
        
    if command_mode == 'antigravity-status':
        from utils.antigravity_oauth import load_tokens, is_oauth_available
        if is_oauth_available():
            tokens = load_tokens()
            console.print("[green]✓ Antigravity OAuth is configured[/green]")
            if tokens:
                console.print(f"  [dim]Email: {tokens.email or 'unknown'}[/dim]")
                console.print(f"  [dim]Project: {tokens.project_id}[/dim]")
                if tokens.is_expired():
                    console.print("  [yellow]Token expired (will auto-refresh)[/yellow]")
                else:
                    console.print("  [dim]Token valid[/dim]")
        else:
            console.print("[yellow]✗ Antigravity OAuth not configured[/yellow]")
            console.print("  [dim]Run 'research antigravity-login' to authenticate[/dim]")
        sys.exit(0)

    # Handle resume mode
    resume_path = None
    if args.resume:
        resume_path = Path(args.resume).resolve()
        if not resume_path.exists():
            console.print(f"[red]Error: Resume directory does not exist: {resume_path}[/red]")
            sys.exit(1)
        
        # Try to extract topic from checkpoint or directory name
        checkpoint_file = resume_path / "artifacts" / "checkpoint.json"
        topic = None
        
        if checkpoint_file.exists():
            try:
                checkpoint = json.loads(checkpoint_file.read_text())
                # Try to get topic from checkpoint data or research plan
                if "topic" in checkpoint.get("data", {}):
                    topic = checkpoint["data"]["topic"]
            except Exception:
                pass
        
        # Fallback: extract from directory name or use provided topic
        if not topic:
            if args.topic:
                topic = " ".join(args.topic)
            else:
                # Extract from directory name (remove timestamp prefix)
                dir_name = resume_path.name
                topic_part = "_".join(dir_name.split("_")[2:])  # Skip YYYYMMDD_HHMMSS
                topic = topic_part.replace("_", " ").title()
                console.print(f"[yellow]No topic provided, extracted from directory: {topic}[/yellow]")
    else:
        topic = " ".join(args.topic)
        # If no topic provided and no resume, default to interactive mode
        if not topic and not args.resume:
            args.interactive = True
            
        if not topic and not args.interactive:
            parser.print_help()
            sys.exit(1)
    
    # Interactive config mode
    if args.interactive:
        config = interactive_config(topic)
        reasoning_model = config["reasoning_model"]
        revisions = config["revisions"]
        if "topic" in config:
            topic = config["topic"]
        # Update phase module iteration limits
        from phases.drafter import set_max_iterations as set_drafter_iterations
        set_drafter_iterations(config["max_iterations"])
        
        # Apply budget from profile if set
        if "budget" in config:
            args.budget = config["budget"]
    else:
        reasoning_model = args.reasoning_model
        revisions = args.revisions
    
    routing = ModelRouting.from_env(
        reasoning_model=reasoning_model,
        rag_model=args.rag_model,
        embedding_model=args.embedding_model,
    )
    set_model_routing(routing)
    
    # Check Gemini OAuth status for Gemini models
    if routing.reasoning_model.startswith("gemini/") or routing.rag_model.startswith("gemini/"):
        from utils.gemini_oauth import is_oauth_available
        if is_oauth_available():
            console.print("[dim]Using Gemini OAuth (your Gemini plan quota)[/dim]")
        else:
            console.print("[dim]Using Gemini API key (no OAuth configured)[/dim]")

    try:
        ensure_model_env(routing.reasoning_model)
        ensure_model_env(routing.rag_model)
        ensure_model_env(routing.embedding_model)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("\n[yellow]Set API keys in .env or use OAuth:[/yellow]")
        console.print("  research gemini-login  # Use your Gemini plan quota")
        console.print("  # or set API key:")
        console.print("  GEMINI_API_KEY=...")
        sys.exit(1)
    
    console.print(f"[dim]Reasoning model: {routing.reasoning_model}[/dim]")
    console.print(f"[dim]RAG model: {routing.rag_model}[/dim]")
    console.print(f"[dim]Embedding model: {routing.embedding_model}[/dim]")
    
    # Determine budget and cost status
    cost_free = False
    if routing.reasoning_model.startswith("gemini/") or routing.rag_model.startswith("gemini/"):
        try:
            from utils.gemini_oauth import is_oauth_available
            if is_oauth_available():
                cost_free = True
                if args.budget is None:
                    args.budget = "high"
                    console.print("[dim]Defaulting to HIGH budget (Gemini OAuth detected)[/dim]")
        except ImportError:
            pass

    if args.budget is None:
        args.budget = "low"

    # Initialize orchestrator with budget mode
    orchestrator = Orchestrator.from_cli(args.budget, cost_free=cost_free)
    set_orchestrator(orchestrator)
    console.print(f"[dim]Budget mode: {orchestrator.budget_mode.value}[/dim]")
    
    # Enable JSON output mode for external tool integration (e.g., WhatsApp bot)
    if args.json_output:
        globals()['_json_output_mode'] = True
        emit_progress("Starting", "in_progress", topic=topic)
    
    try:
        generate_report(topic, max_revisions=revisions, num_reviewers=args.reviewers, resume_from=resume_path)
    except Exception as e:
        console.print(f"[bold red]Fatal Error: {e}[/bold red]")
        # Try to send notification if UI was initialized or create temp one
        from utils.ui import get_ui, UIManager
        ui = get_ui()
        if not ui:
            ui = UIManager("Error Handler", "None")
        ui.send_notification(f"Research failed: {str(e)}", "Research Agent Error", urgent=True)
        sys.exit(1)
    
    # Print cost summary at end
    orchestrator.print_summary()

