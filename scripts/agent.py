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

def generate_report(topic: str, max_revisions: int = 3, num_reviewers: int = 1) -> Path:
    """Generate a research report with planning, review, and revision phases."""
    global _used_citation_keys, _debug_logger, _session_start_time
    _used_citation_keys = set()
    _session_start_time = time.time()  # Track session start for timeout
    
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
    
    # Create report directory early
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
    
    # Check for existing checkpoint (resume capability)
    existing_checkpoint = load_checkpoint()
    if existing_checkpoint:
        console.print(Panel(
            f"[yellow]📂 Found checkpoint from previous run[/yellow]\n\n"
            f"Phase: {existing_checkpoint['phase']}\n"
            f"Time: {existing_checkpoint.get('timestamp', 'unknown')}\n\n"
            f"[dim]Note: Resume functionality will skip to the last completed phase.\n"
            f"The checkpoint system is currently for recovery only.[/dim]",
            title="Previous Progress Detected",
            border_style="yellow"
        ))
        console.print("[dim]Continuing with fresh run (resume in future version)...[/dim]\n")
    
    # Create artifacts subfolder
    artifacts_dir = report_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    # Setup debug logging
    _debug_logger = setup_debug_log(report_dir)
    log_debug(f"Starting research session: {topic}")
    log_debug(f"Max revisions: {max_revisions}")
    
    # ========== PHASE 1: PLANNING ==========
    console.print(Panel(
        f"[bold blue]Phase 1: Research Planning[/bold blue]",
        border_style="blue", width=60
    ))
    
    research_plan = create_research_plan(topic)
    save_checkpoint("research_plan", {"plan": research_plan, "library_size": len(list(LIBRARY_PATH.rglob("*.pdf")))})
    # Save plan to artifacts
    (artifacts_dir / "research_plan.json").write_text(json.dumps(research_plan, indent=2))
    log_debug(f"Research plan created: {json.dumps(research_plan)}")
    
    # ========== PHASE 1b: ARGUMENT DISSECTION ==========
    console.print(Panel(
        f"[bold magenta]Phase 1b: Argument Dissection[/bold magenta]",
        border_style="magenta", width=60
    ))
    
    argument_map = create_argument_map(topic, research_plan)
    save_checkpoint("argument_map", {"map": argument_map})
    (artifacts_dir / "argument_map.json").write_text(json.dumps(argument_map, indent=2))
    log_debug(f"Argument map created: {json.dumps(argument_map)}")
    
    # ========== PHASE 2: RESEARCH & WRITE ==========
    console.print(Panel(
        f"[bold cyan]Phase 2: Research & Writing[/bold cyan]",
        border_style="cyan", width=60
    ))
    
    try:
        typst_content = run_agent(topic, research_plan=research_plan, argument_map=argument_map)
    except RuntimeError as e:
        # Fail fast on model access issues; don't continue generating empty drafts/reviews.
        console.print(f"[red]Fatal LLM error: {e}[/red]")
        raise

    if not typst_content or typst_content.strip().startswith("// Agent did not produce"):
        raise RuntimeError("Agent failed to produce a draft. Aborting to avoid generating empty reports.")
    
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
        compile_and_fix(artifacts_dir / "draft_initial.typ")
    except Exception:
        pass  # Don't fail if typst not available
    
    log_debug(f"Initial draft complete with {len(all_cited)} citations")
    
    # ========== PHASE 3: PEER REVIEW LOOP ==========
    reviews = []
    round_reviews_history = []
    
    for revision_round in range(1, max_revisions + 1):
        # Check session timeout at start of each revision round
        if check_session_timeout():
            console.print("[yellow]⚠ Skipping further revisions due to session timeout[/yellow]")
            break
            
        console.print(Panel(
            f"[bold magenta]Phase 3.{revision_round}: Peer Review[/bold magenta]",
            border_style="magenta", width=60
        ))
        
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
            console.print("[bold green]✓ Paper accepted by all reviewers![/bold green]")
            break
            
        # Process Recommended Papers from Reviewers
        all_recommendations = []
        for rr in round_reviews:
            if "recommended_papers" in rr:
                all_recommendations.extend(rr["recommended_papers"])
        
        added_citations = []
        if all_recommendations:
            console.print(Panel(f"[bold blue]Processing {len(all_recommendations)} Reviewer Recommendations[/bold blue]"))
            for rec in all_recommendations:
                # Handle DOI
                if "doi" in rec:
                    try:
                        console.print(f"[cyan]Adding recommended DOI: {rec['doi']}[/cyan]")
                        add_paper(identifier=rec['doi'], source="doi")
                        added_citations.append(f"DOI: {rec['doi']} (Reason: {rec.get('reason', 'Reviewer recommended')})")
                    except Exception as e:
                        console.print(f"[red]Failed to add DOI {rec['doi']}: {e}[/red]")
                # Handle Query
                elif "query" in rec:
                    try:
                        console.print(f"[cyan]Discovering for query: {rec['query']}[/cyan]")
                        # First discover
                        found = discover_papers(query=rec['query'], limit=3)
                        # Then auto-add top 1 if found
                        if found and found[0].get("title"): # Check if valid result
                             first_paper = found[0]
                             ident = first_paper.get("doi") or first_paper.get("arxivId") or first_paper.get("arxiv_id")
                             if ident:
                                 console.print(f"[cyan]Adding discovered paper: {ident}[/cyan]")
                                 add_paper(identifier=ident, source="doi" if first_paper.get("doi") else "arxiv")
                                 added_citations.append(f"Discovered: {first_paper.get('title')} ({ident})")
                    except Exception as e:
                        console.print(f"[red]Failed to process query {rec['query']}: {e}[/red]")

        # Synthesize feedback string
        combined_feedback = f"Processing {len(round_reviews)} reviews.\n\n"
        
        if added_citations:
             console.print("[bold blue]Wait! Indexing new papers and generating summaries...[/bold blue]")
             
             # Force update index (by calling query_library widely)
             # And try to get summaries for new papers to inject into prompt
             paper_summaries = []
             try:
                 # Construct a query to get summaries of new papers
                 # We can't query by ID easily, but we can list library and then partial match?
                 # Better: Just run a broad query relevant to the REASONS given
                 # Or just generic "What are the key findings of [Title]?"
                 
                 for ac_text in added_citations:
                     # ac_text format: "Discovered: Title (ID)" or "DOI: ID (Reason)"
                     if "Discovered:" in ac_text:
                         title_part = ac_text.split("Discovered:")[1].split("(")[0].strip()
                         query_text = f"What are the key findings of the paper '{title_part}'?"
                     else:
                         doi_part = ac_text.split("DOI:")[1].split("(")[0].strip()
                         query_text = f"What are the key findings of the paper with DOI {doi_part}?"
                     
                     # Check if we should query
                     console.print(f"[dim]Indexing & Summarizing: {query_text[:50]}...[/dim]")
                     answer = query_library(query_text) # This triggers indexing!
                     if answer and "I cannot answer" not in answer:
                         paper_summaries.append(f"**Paper**: {ac_text}\n**Summary**: {answer}\n")
             except Exception as e:
                 console.print(f"[yellow]Warning: Failed to summarize new papers: {e}[/yellow]")

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
        console.print(Panel(f"[bold magenta]Review Round {revision_round} Summaries[/bold magenta]", border_style="magenta"))
        
        # Calculate aggregated verdict (most common verdict, or worst case)
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
            verdict_color = "green" if verdict == 'accept' else "yellow"
            
            console.print(f"[bold]Reviewer {i}:[/bold] [{verdict_color}]{verdict.upper()}[/{verdict_color}]")
            console.print(f"[italic]{rr.get('summary', '')}[/italic]")
            if rr.get('weaknesses'):
                console.print(f"[red]• {len(rr.get('weaknesses'))} Weaknesses identified[/red]")
            if rr.get('matching_citations') or rr.get('missing_citations'):
                 console.print(f"[blue]• Citation feedback provided[/blue]")
            console.print("")

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
        console.print(Panel(
            f"[bold yellow]Phase 4.{revision_round}: Revision[/bold yellow]",
            border_style="yellow", width=60
        ))
        
        typst_content = revise_document(
            typst_content, 
            combined_feedback, 
            topic,
            research_plan
        )
        
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
            compile_and_fix(artifacts_dir / f"draft_r{revision_round}.typ")
        except Exception:
            pass  # Don't fail if typst not available
        
        # Checkpoint after revision
        save_checkpoint(f"revision_r{revision_round}", {
            "round": revision_round,
            "document": typst_content,
            "citations": list(_used_citation_keys)
        })
        
        log_debug(f"Revision {revision_round} complete with {len(all_cited)} citations")
    
    # ========== FINAL OUTPUT ==========
    console.print(Panel(
        f"[bold green]Finalizing Output[/bold green]",
        border_style="green", width=60
    ))
    
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
    
    # Inject star_hash parameter into the document
    if 'star_hash:' not in typst_content:
        typst_content = typst_content.replace(
            'date:',
            'star_hash: "star_hash.svg",\n  date:'
        )
    
    # Write final main.typ
    main_typ = report_dir / "main.typ"
    main_typ.write_text(typst_content)
    
    # Copy compile.sh and compile using it (generates star hash + PDF)
    compile_script = TEMPLATE_PATH / "compile.sh"
    if compile_script.exists():
        shutil.copy(compile_script, report_dir / "compile.sh")
        (report_dir / "compile.sh").chmod(0o755)
    
    with console.status("[dim]Compiling PDF (with star hash)..."):
        try:
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
                # Fallback to plain typst if compile.sh not available
                result = subprocess.run(
                    ["typst", "compile", "main.typ"],
                    cwd=report_dir,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
            if result.returncode != 0:
                log_debug(f"Compile error: {result.stderr}")
        except FileNotFoundError:
            log_debug("compile.sh or typst not found")
        except Exception as e:
            log_debug(f"Compile error: {e}")
    
    # Export literature review sheet
    literature_sheet = export_literature_sheet()
    (report_dir / "literature_sheet.csv").write_text(literature_sheet)
    # Markdown sheet removed as requested
    log_debug(f"Literature sheet exported with {len(get_reviewed_papers())} papers")
    
    # Summary
    reviewed_count = len(get_reviewed_papers())
    # Robust citation count from used keys directly
    cited_count = len(get_used_citation_keys())
    
    console.print("\n" + "="*60)
    console.print(Panel(
        f"[bold green]✓ Research Complete[/bold green]\n\n"
        f"[white]Topic:[/white] {topic[:50]}...\n"
        f"[white]Reviews:[/white] {len(reviews)} round{'s' if len(reviews) != 1 else ''}\n"
        f"[white]Final verdict:[/white] {reviews[-1]['verdict'].upper() if reviews else 'N/A'}\n"
        f"[white]Papers:[/white] {cited_count} cited / {reviewed_count} reviewed\n\n"
        f"[dim]Output:[/dim]\n"
        f"  📝 main.typ\n"
        f"  📄 main.pdf\n"
        f"  📚 refs.bib\n"
        f"  📊 literature_sheet.csv\n"
        f"  📁 artifacts/ (plans, drafts, reviews)\n\n"
        f"[dim]{report_dir}[/dim]",
        border_style="green"
    ))
    
    log_debug(f"Session complete: {report_dir}")
    return report_dir


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def interactive_config(topic: str) -> dict:
    """Show interactive config menu before research starts."""
    from rich.prompt import Prompt, IntPrompt
    
    console.print()
    console.print(Panel(
        f"[bold cyan]Research Agent Config[/bold cyan]\n\n"
        f"[white]Topic:[/white] {topic[:60]}{'...' if len(topic) > 60 else ''}",
        border_style="cyan"
    ))
    console.print()
    
    # Model selection
    model = Prompt.ask(
        "[cyan]Model[/cyan]",
        choices=["3-pro", "2.5-flash", "2.5-pro"],
        default="2.5-flash"
    )
    model_map = {
        "3-pro": "gemini/gemini-3-pro-preview",
        "2.5-flash": "gemini/gemini-2.5-flash",
        "2.5-pro": "gemini/gemini-2.5-pro-preview",
    }
    
    # Iterations and revisions
    max_iterations = IntPrompt.ask("[cyan]Max agent iterations[/cyan]", default=50)
    revisions = IntPrompt.ask("[cyan]Revision rounds[/cyan]", default=3)
    
    console.print()
    return {
        "reasoning_model": model_map[model],
        "max_iterations": max_iterations,
        "revisions": revisions,
    }


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Autonomous Research Agent with Peer Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  research agent "Impact of attention mechanisms on NLP"
  research agent -r 5 "Vision Transformers vs CNNs"
  research agent --revisions 1 "Quick research topic"
  research agent -i "Interactive config mode"
  research agent --reasoning-model openai/gpt-5.2-high --rag-model openai/gpt-5.2-fast "Topic"
        """
    )
    parser.add_argument('topic', nargs='+', help='Research topic')
    parser.add_argument('--interactive', '-i', action='store_true',
                       help='Show config menu before starting')
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
    
    args = parser.parse_args()
    topic = " ".join(args.topic)
    
    if not topic:
        parser.print_help()
        sys.exit(1)
    
    # Interactive config mode
    if args.interactive:
        config = interactive_config(topic)
        reasoning_model = config["reasoning_model"]
        revisions = config["revisions"]
        # Update phase module iteration limits
        from phases.drafter import set_max_iterations as set_drafter_iterations
        set_drafter_iterations(config["max_iterations"])
    else:
        reasoning_model = args.reasoning_model
        revisions = args.revisions
    
    routing = ModelRouting.from_env(
        reasoning_model=reasoning_model,
        rag_model=args.rag_model,
        embedding_model=args.embedding_model,
    )
    set_model_routing(routing)

    try:
        ensure_model_env(routing.reasoning_model)
        ensure_model_env(routing.rag_model)
        ensure_model_env(routing.embedding_model)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("\n[yellow]Set API keys in .env:[/yellow]")
        console.print("  OPENAI_API_KEY=...")
        console.print("  # or for Gemini:")
        console.print("  GEMINI_API_KEY=...")
        sys.exit(1)
    
    console.print(f"[dim]Reasoning model: {routing.reasoning_model}[/dim]")
    console.print(f"[dim]RAG model: {routing.rag_model}[/dim]")
    console.print(f"[dim]Embedding model: {routing.embedding_model}[/dim]")
    
    generate_report(topic, max_revisions=revisions, num_reviewers=args.reviewers)

