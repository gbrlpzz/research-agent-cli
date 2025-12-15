"""
Revision phase for research agent.

Contains the revision agent loop that incorporates peer review feedback.
"""
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List

from rich.console import Console
from rich.panel import Panel

# Import from parent utils
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm import llm_chat, _safe_json_loads
from utils.ui import get_ui
from .tool_registry import TOOLS, TOOL_FUNCTIONS


console = Console()

# Model will be set by agent.py
AGENT_MODEL = "gemini/gemini-3-pro-preview"

# Configurable limits
MAX_REVISION_ITERATIONS = int(os.getenv('REVISION_MAX_ITERATIONS', '25'))
API_TIMEOUT_SECONDS = int(os.getenv('API_TIMEOUT_SECONDS', '120'))


def set_model(model: str) -> None:
    """Set the model to use for revising."""
    global AGENT_MODEL
    AGENT_MODEL = model


def set_max_iterations(n: int) -> None:
    """Set the maximum number of revision iterations."""
    global MAX_REVISION_ITERATIONS
    MAX_REVISION_ITERATIONS = n


# Revision prompt template
REVISION_PROMPT = """You are revising an academic Typst document based on peer review feedback.

Standard: MAXIMUM RIGOR. It is better to delete/soften an unsupported claim than to keep it.

Your tasks:
1) Read the ORIGINAL document carefully
2) Study the PEER REVIEW feedback
3) Create an IMPLEMENTATION PLAN (3-8 bullets) mapping each major issue -> concrete fix
4) Produce the REVISED Typst document

Hard rules:
- You MAY remove claims, paragraphs, sections, or citations if they are unsupported or misleading.
- Do NOT preserve a citation just to keep density; every citation must be relevant to the local claim.
- Any newly introduced factual claim must be evidence-grounded (query_library).
- Before final output, ensure all @citation_keys are valid (validate_citations) and fix/remove any invalid ones.

Output: the complete revised Typst document (no commentary besides the implementation plan requested by the user prompt)."""


def revise_document(
    original: str,
    review: str,
    topic: str,
    research_plan: Dict
) -> str:
    """
    Revise document based on peer review feedback.
    
    Returns the revised Typst document content.
    """
    ui = get_ui()
    if ui:
        ui.log("Addressing peer review feedback...", "INFO")
    else:
        console.print(Panel(
            f"[bold yellow]✏️ Revision Phase[/bold yellow]\n\n"
            f"Addressing peer review feedback...",
            border_style="yellow"
        ))
    
    current_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    user_prompt = f"""Revise this document based on peer review.

TOPIC: {topic}

RESEARCH PLAN:
{json.dumps(research_plan, indent=2)}

ORIGINAL DOCUMENT:
```typst
{original}
```

PEER REVIEW:
{review}

First, create a brief IMPLEMENTATION PLAN (3-5 bullet points).
Then output the complete REVISED Typst document.
Use date: "{current_date}" in the document.
"""

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": REVISION_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    
    # Run revision loop
    max_iterations = MAX_REVISION_ITERATIONS
    for iteration in range(max_iterations):
        if ui:
            ui.set_status(f"Revising (step {iteration + 1})...")
        else:
            # Fallback for no UI
            pass

        try:
            # If no UI, use console.status context manager, else just call API
            if not ui:
                with console.status(f"[cyan]Revising (step {iteration + 1})..."):
                    assistant_msg = llm_chat(
                        model=AGENT_MODEL,
                        messages=messages,
                        tools=TOOLS,
                        temperature=None,
                        timeout_seconds=API_TIMEOUT_SECONDS,
                    )
            else:
                assistant_msg = llm_chat(
                    model=AGENT_MODEL,
                    messages=messages,
                    tools=TOOLS,
                    temperature=None,
                    timeout_seconds=API_TIMEOUT_SECONDS,
                )
        except Exception as e:
            if ui: ui.log(f"API error: {e}", "ERROR")
            else: console.print(f"[red]API error: {e}[/red]")
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
                
                if ui:
                    ui.log(f"Revision tool: {fn}", "DEBUG")
                else:
                    console.print(f"[yellow]  → {fn}(...)[/yellow]")
                
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

        text = (assistant_msg.get("content") or "").strip()
        if not text:
            if ui: ui.log("Empty revision response, retrying...", "WARNING")
            else: console.print("[yellow]Empty revision response, retrying...[/yellow]")
            continue
        
        # Check for complete document markers
        if "#import" in text and "project.with" in text and "#bibliography" in text:
            if ui: ui.log("Revised document generated", "SUCCESS")
            else: console.print("[green]✓ Revised document generated[/green]")
            
            # Extract from code block if present
            if "```typst" in text:
                match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                if match:
                    return match.group(1).strip()
            return text
        
        # Continue prompting
        messages.append({"role": "assistant", "content": text})
        messages.append(
            {
                "role": "user",
                "content": "Continue. Output the complete revised Typst document.",
            }
        )
    
    return original  # Fallback to original if revision fails


__all__ = ["revise_document", "set_model", "set_max_iterations", "REVISION_PROMPT"]
