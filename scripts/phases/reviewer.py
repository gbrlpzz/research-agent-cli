"""
Peer review phase for research agent.

Contains the reviewer agent loop that evaluates Typst documents.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel

# Import from parent utils
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm import llm_chat, _safe_json_loads
from .tool_registry import REVIEWER_TOOLS, TOOL_FUNCTIONS


console = Console()

# Model will be set by agent.py
AGENT_MODEL = "gemini/gemini-3-pro-preview"

# Configurable limits
MAX_REVIEWER_ITERATIONS = int(os.getenv('MAX_REVIEWER_ITERATIONS', '15'))
REVIEWER_TIMEOUT_SECONDS = int(os.getenv('REVIEWER_TIMEOUT_SECONDS', '180'))


def set_model(model: str) -> None:
    """Set the model to use for reviewing."""
    global AGENT_MODEL
    AGENT_MODEL = model


def set_max_iterations(n: int) -> None:
    """Set the maximum number of reviewer iterations."""
    global MAX_REVIEWER_ITERATIONS
    MAX_REVIEWER_ITERATIONS = n


# Reviewer prompt template
REVIEWER_PROMPT = """You are a rigorous academic peer reviewer.
You are reviewing a Typst document on: "{topic}"

Standard: MAXIMUM RIGOR. Reject/require revisions rather than letting unsupported claims pass.
You have tools to verify claims, check citations, and identify missing literature.

## Mandatory checks (use tools)
1) Citation validity:
   - Extract all @citation_keys you see and run validate_citations([..all keys..]).
   - Any invalid key is a critical flaw unless removed/fixed.

2) ***Citation density (CRITICAL)***:
   - Count the total unique @citation_keys in the document.
   - If fewer than 10 unique papers are cited: MAJOR REVISIONS (require author to discover + cite more).
   - If fewer than 5 unique papers are cited: REJECT (insufficient scholarship).
   - Each substantive paragraph should have 3-5 citations. Flag any paragraph with <2 as under-cited.

3) Claim verification:
   - Select at least 5 concrete claims across different sections.
   - For each, run query_library() with the claim phrased as a question.
   - If the returned evidence does NOT support the claim, flag it as unsupported/overstated.

4) Citation relevance (anti-citation-hallucination):
   - If a paragraph is cited but the claim seems mismatched, query_library() to check whether the cited literature is actually about that claim.
   - Flag "irrelevant citation padding" separately from "missing citation".

5) Coverage + counter-arguments:
   - If the document is one-sided, require major revisions or reject.
   - If key counter-arguments are missing, recommend specific search queries (or DOIs if known).

6) Typst integrity:
   - Flag Markdown-style syntax (**bold, # headings, [@key]) or missing #bibliography("refs.bib").

7) Overclaim + scope control:
   - Flag causal language that is not warranted (e.g., "causes", "proves", "settles").
   - Flag scope creep beyond the stated topic/research plan (claims not addressed by evidence).
   - Flag rhetorical overstatement or missing uncertainty qualifiers where evidence is weak.

## Previous Reviews
If provided, check if the author addressed:
{previous_reviews}

## Output Format (keep these exact headings)
**VERDICT**: [Accept | Minor Revisions | Major Revisions | Reject]

**SUMMARY**: 
Brief assessment of quality, central contributions, and whether claims are evidence-grounded.

**STRENGTHS**:
- ...

**WEAKNESSES**:
- ...

**RECOMMENDED PAPERS** (only if needed):
Write ONE line per recommendation in EXACTLY one of these formats:
RECOMMEND DOI: 10.xxxx/yyyy | Reason: Why this paper is needed
RECOMMEND SEARCH: "search query terms" | Reason: Why these papers are needed

**SPECIFIC EDITS**:
For each critical issue, use:
Section: ...
Issue: ...
Suggestion: ...

Be constructive but uncompromising on evidence and citation correctness.
"""


def peer_review(
    typst_content: str, 
    topic: str, 
    round_num: int, 
    reviewer_id: int, 
    research_plan: Dict,
    refs_bib: str,
    previous_reviews: str = ""
) -> Dict[str, Any]:
    """
    Conduct a peer review of the document using an LLM agent with tools.
    
    Returns a dictionary with verdict, summary, weaknesses, and recommendations.
    """
    console.print(Panel(
        f"[bold blue]🔍 Reviewer #{reviewer_id} (Round {round_num})[/bold blue]\n\n"
        f"Verifying document on: {topic[:60]}...",
        border_style="blue"
    ))
    
    # Context for the reviewer
    context = f"""
    TOPIC: {topic}
    
    RESEARCH PLAN:
    {json.dumps(research_plan, indent=2)}
    
    BIBLIOGRAPHY (refs.bib):
    {refs_bib}
    
    DOCUMENT CONTENT (Typst):
    {typst_content}
    """
    
    # Format message
    user_msg = f"Please review this document (Round {round_num})."
    if previous_reviews:
        user_msg += f"\n\nHere is the feedback from the previous round waiting to be addressed:\n{previous_reviews}"

    system_msg = REVIEWER_PROMPT.format(topic=topic, previous_reviews=previous_reviews)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": context + "\n\n" + user_msg},
    ]
    
    # Reviewer loop (configurable iteration limit to prevent infinite loops)
    max_steps = MAX_REVIEWER_ITERATIONS
    step = 0
    final_review = None
    
    while step < max_steps:
        step += 1
        try:
            assistant_msg = llm_chat(
                model=AGENT_MODEL,
                messages=messages,
                tools=REVIEWER_TOOLS,
                temperature=None,
                timeout_seconds=REVIEWER_TIMEOUT_SECONDS,
            )
        except Exception as e:
            console.print(f"[red]Reviewer API error: {e}[/red]")
            break
            
        tool_calls = assistant_msg.get("tool_calls") or []
        if tool_calls:
            assistant_history = {
                "role": "assistant",
                "content": assistant_msg.get("content") or "",
                "tool_calls": tool_calls,
            }
            if "raw_gemini_parts" in assistant_msg:
                assistant_history["raw_gemini_parts"] = assistant_msg["raw_gemini_parts"]
            messages.append(assistant_history)

            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name")
                args = _safe_json_loads((tc.get("function") or {}).get("arguments"))
                tc_id = tc.get("id")
                
                console.print(f"[magenta]  Reviewer: {fn}(...)[/magenta]")
                
                if fn in TOOL_FUNCTIONS:
                    try:
                        result = TOOL_FUNCTIONS[fn](**args)
                    except Exception as e:
                        result = {"error": str(e)}
                else:
                    result = {"error": f"Unknown function: {fn}"}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(result, default=str),
                    }
                )
            continue

        # Text response - the review in plain text format
        text = (assistant_msg.get("content") or "").strip()
        
        if not text:
            # Empty response - nudge for output
            if step >= max_steps - 3:
                messages.append({"role": "user", "content": "Please provide your FINAL review now with **VERDICT**: [Accept | Minor Revisions | Major Revisions | Reject]"})
            continue
        
        # Lenient verdict parsing - try multiple patterns
        verdict = None
        verdict_patterns = [
            r"\*\*VERDICT\*\*:\s*\[?([^\]\n]+)",     # **VERDICT**: [value]
            r"VERDICT:\s*\[?([^\]\n]+)",             # VERDICT: value
            r"#+ ?VERDICT:?\s*\[?([^\]\n]+)",        # # VERDICT or ## VERDICT
            r"Verdict:\s*\[?([^\]\n]+)",             # Verdict: value
        ]
        
        for pattern in verdict_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                verdict_raw = match.group(1).strip().lower()
                if "reject" in verdict_raw:
                    verdict = "reject"
                elif "major" in verdict_raw:
                    verdict = "major_revisions"
                elif "accept" in verdict_raw and "minor" not in verdict_raw:
                    verdict = "accept"
                else:
                    verdict = "minor_revisions"
                break
        
        # Last resort: look for verdict keywords in first 500 chars
        if not verdict:
            first_chunk = text[:500].lower()
            if "reject" in first_chunk:
                verdict = "reject"
            elif "major revision" in first_chunk:
                verdict = "major_revisions"
            elif "minor revision" in first_chunk:
                verdict = "minor_revisions"
            elif "accept" in first_chunk:
                verdict = "accept"
        
        if verdict:
            # Extract summary (lenient)
            summary_patterns = [
                r"\*\*SUMMARY\*\*:?\s*\n?(.*?)(?=\n\*\*|\n#|$)",
                r"SUMMARY:?\s*\n?(.*?)(?=\n\*\*|\n#|$)",
                r"#+ ?SUMMARY:?\s*\n?(.*?)(?=\n#|$)",
            ]
            summary = text[:300]  # Fallback
            for pattern in summary_patterns:
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match and match.group(1).strip():
                    summary = match.group(1).strip()[:500]
                    break
            
            # Extract weaknesses (lenient)
            weaknesses = ""
            weakness_patterns = [
                r"\*\*WEAKNESSES?\*\*:?\s*\n?(.*?)(?=\n\*\*|\n#|$)",
                r"WEAKNESSES?:?\s*\n?(.*?)(?=\n\*\*|\n#|$)",
                r"#+ ?WEAKNESSES?:?\s*\n?(.*?)(?=\n#|$)",
            ]
            for pattern in weakness_patterns:
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match and match.group(1).strip():
                    weaknesses = match.group(1).strip()
                    break
            
            # Extract recommended papers
            recommendations = []
            for line in text.split("\n"):
                if "RECOMMEND DOI:" in line.upper():
                    doi_match = re.search(r"(?:RECOMMEND )?DOI:?\s*(10\.\S+)", line, re.IGNORECASE)
                    reason_match = re.search(r"Reason:\s*(.+)", line, re.IGNORECASE)
                    if doi_match:
                        recommendations.append({
                            "doi": doi_match.group(1).strip().rstrip('.,;'),
                            "reason": reason_match.group(1).strip() if reason_match else "Recommended by reviewer",
                        })
                elif "RECOMMEND SEARCH:" in line.upper():
                    query_match = re.search(r"SEARCH:?\s*[\"']([^\"']+)[\"']", line, re.IGNORECASE)
                    reason_match = re.search(r"Reason:\s*(.+)", line, re.IGNORECASE)
                    if query_match:
                        recommendations.append({
                            "query": query_match.group(1).strip(),
                            "reason": reason_match.group(1).strip() if reason_match else "Recommended by reviewer",
                        })
            
            final_review = {
                "verdict": verdict,
                "summary": summary,
                "weaknesses": weaknesses,
                "recommended_papers": recommendations,
                "full_text": text,
            }
            console.print(f"[bold]Reviewer #{reviewer_id} Verdict: {verdict.upper()}[/bold]")
            break  # Done!
        
        # No verdict found yet - append and continue
        messages.append({"role": "assistant", "content": text})
        
        # Nudge for structured output near the end
        if step >= max_steps - 3:
            messages.append({
                "role": "user", 
                "content": "Please provide your FINAL review. Use this EXACT format:\n**VERDICT**: [Accept | Minor Revisions | Major Revisions | Reject]\n**SUMMARY**: Your assessment\n**WEAKNESSES**: List issues\n**SPECIFIC EDITS**: What to fix"
            })
        elif step == max_steps - 1:
            messages.append({"role": "user", "content": "Please provide your final review now."})

    if not final_review:
        # Fallback - capture whatever text we have for debugging
        last_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_text = msg["content"][:500]
                break
        
        console.print(f"[yellow]⚠ Reviewer did not produce structured review[/yellow]")
        final_review = {
            "verdict": "minor_revisions",
            "summary": f"Reviewer did not produce structured review. Last output: {last_text[:200]}..." if last_text else "Reviewer did not produce structured review",
            "weaknesses": "",
            "recommended_papers": [],
            "full_text": last_text
        }
        
    return {
        "reviewer_id": reviewer_id,
        "round": round_num,
        **final_review
    }


__all__ = ["peer_review", "set_model", "set_max_iterations", "REVIEWER_PROMPT"]
