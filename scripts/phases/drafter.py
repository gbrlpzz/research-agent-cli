"""
Drafting phase for research agent.

Contains the main tool-calling agent loop that generates the initial Typst document.
"""
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from rich.console import Console
from rich.panel import Panel

# Import from parent utils
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm import llm_chat, _safe_json_loads
from utils.prompts import SYSTEM_PROMPT
from .tool_registry import TOOLS, TOOL_FUNCTIONS, get_reviewed_papers


console = Console()

# Model will be set by agent.py
AGENT_MODEL = "gemini/gemini-3-pro-preview"

# Configurable limits (can be overridden)
MAX_AGENT_ITERATIONS = int(os.getenv('AGENT_MAX_ITERATIONS', '50'))
API_TIMEOUT_SECONDS = int(os.getenv('API_TIMEOUT_SECONDS', '120'))


def set_model(model: str) -> None:
    """Set the model to use for drafting."""
    global AGENT_MODEL
    AGENT_MODEL = model


def set_max_iterations(n: int) -> None:
    """Set the maximum number of agent iterations."""
    global MAX_AGENT_ITERATIONS
    MAX_AGENT_ITERATIONS = n


def run_agent(
    topic: str,
    research_plan: Optional[Dict[str, Any]] = None,
    argument_map: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Run the research agent on a topic with optional research plan and argument map.
    
    Returns the generated Typst document content.
    """
    # Get current date for the document
    current_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    console.print(Panel(
        f"[bold cyan]🤖 Research Agent[/bold cyan]\n\n"
        f"[white]{topic}[/white]\n\n"
        f"[dim]Model: {AGENT_MODEL}[/dim]",
        border_style="cyan"
    ))
    
    # Inject current date into system prompt
    system_prompt_with_date = SYSTEM_PROMPT.replace("CURRENT_DATE", current_date).replace("{current_date}", current_date)
    
    # Build user prompt with optional research plan and argument map
    plan_section = ""
    if research_plan:
        plan_section = f"""
RESEARCH PLAN:
- Main Question: {research_plan.get('main_question', topic)}
- Sub-questions: {', '.join(research_plan.get('sub_questions', [])[:3])}
- Key Concepts: {', '.join(research_plan.get('key_concepts', [])[:5])}
- Search Queries: {', '.join(research_plan.get('search_queries', [])[:3])}
"""
    
    argument_section = ""
    if argument_map:
        argument_section = f"""
ARGUMENT MAP (Evidence Requirements):
- Thesis: {argument_map.get('thesis', 'N/A')}
- Claims to Support:
"""
        for claim in argument_map.get('claims', [])[:5]:
            argument_section += f"  {claim['id']}: {claim['claim']}\n"
            if claim.get('evidence_needed'):
                argument_section += f"    Evidence: {', '.join(claim['evidence_needed'])}\n"
            if claim.get('counter_arguments'):
                argument_section += f"    Counter-args: {', '.join(claim['counter_arguments'])}\n"
    
    user_prompt = f"""Research this topic and produce a Typst document:

TOPIC: {topic}
{plan_section}{argument_section}
IMPORTANT - Follow the enhanced RAG-First workflow:
1. FIRST query_library() with the main topic to see what knowledge already exists
2. Identify gaps in the existing knowledge (especially for argument map claims)
3. Use BOTH keyword search AND citation networks to discover papers
4. add_paper() for the most relevant papers
5. query_library() again with more specific questions
6. Follow multi-pass drafting (outline → sections → integration)
7. Self-critique before finalizing
8. fuzzy_cite() to get @citation_keys
9. Output complete Typst document (use date: "{current_date}")"""

    # OpenAI-style messages (system prompt is updated dynamically each step)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt_with_date},
        {"role": "user", "content": user_prompt},
    ]
    
    console.print("\n[bold]Starting autonomous research...[/bold]\n")
    
    max_iterations = MAX_AGENT_ITERATIONS
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Update system prompt with latest available citations (Dynamic Injection)
        current_papers = get_reviewed_papers()
        citation_section = ""
        if current_papers:
            citation_list = []
            for _, data in current_papers.items():
                ck = (data.get("citation_key") or "").strip()
                if not ck:
                    continue
                citation_list.append(f"- @{ck}")
            
            citation_section = "\n\n## AVAILABLE CITATION KEYS (Use ONLY these exact keys):\n" + "\n".join(citation_list[:30])
            
        current_system_prompt = system_prompt_with_date + citation_section
        
        # Update system prompt (first message) with latest citations
        messages[0]["content"] = current_system_prompt
        
        with console.status(f"[cyan]Thinking (step {iteration}/{max_iterations})..."):
            try:
                assistant_msg = llm_chat(
                    model=AGENT_MODEL,
                    messages=messages,
                    tools=TOOLS,
                    temperature=None,
                    timeout_seconds=API_TIMEOUT_SECONDS,
                )
            except Exception as e:
                console.print(f"[red]API error: {e}[/red]")
                break
        
        tool_calls = assistant_msg.get("tool_calls") or []
        if tool_calls:
            # Preserve the tool_calls on the assistant message (OpenAI tool-calling protocol)
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_msg.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )

            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name")
                args = _safe_json_loads((tc.get("function") or {}).get("arguments"))
                tc_id = tc.get("id")
                
                console.print(f"[yellow]→ {fn}({json.dumps(args, default=str)[:80]})[/yellow]")
                
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
            console.print("[yellow]Empty response, retrying...[/yellow]")
            continue
        
        if "#import" in text and "project.with" in text and "#bibliography" in text:
            console.print("[green]✓ Document generated[/green]")
            if "```typst" in text:
                match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                if match:
                    return match.group(1).strip()
            return text
        
        console.print(f"[dim]{text[:150]}...[/dim]")
                
        messages.append({"role": "assistant", "content": text})
        messages.append(
            {
                "role": "user",
                "content": "Continue. Output the complete Typst document when ready.",
            }
        )
    
    return "// Agent did not produce a document within iteration limit"


__all__ = ["run_agent", "set_model", "set_max_iterations"]
