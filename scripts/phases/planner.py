"""
Research planning phase.

Functions for creating research plans and argument maps before main research.
"""
import json
import re
import time
from typing import Any, Dict

from rich.console import Console
from rich.panel import Panel

# Import from parent utils (will work when called from agent.py context)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm import llm_chat, API_TIMEOUT_SECONDS
from utils.prompts import PLANNER_PROMPT, ARGUMENT_DISSECTION_PROMPT
from utils.model_config import ModelRouting
from utils.ui import get_ui

console = Console()

# Model will be set by agent.py
AGENT_MODEL = "gemini/gemini-3-pro-preview"


def set_model(model: str):
    """Set the model to use for planning."""
    global AGENT_MODEL
    AGENT_MODEL = model


def create_research_plan(topic: str) -> Dict[str, Any]:
    """Create a structured research plan before starting research."""
    ui = get_ui()
    if ui:
        ui.log(f"Creating research plan for: {topic}", "INFO")
    else:
        console.print(Panel(
            f"[bold blue]📋 Research Planner[/bold blue]\n\n"
            f"Creating research plan for:\n[white]{topic}[/white]",
            border_style="blue"
        ))
    
    default_plan = {
        "main_question": topic,
        "sub_questions": [topic],
        "key_concepts": [],
        "expected_sections": ["Introduction", "Analysis", "Discussion", "Conclusion"],
        "search_queries": [topic]
    }
    
    # Retry up to 3 times for API issues
    max_retries = 3
    text = ""
    for attempt in range(max_retries):
        try:
            assistant_msg = llm_chat(
                model=AGENT_MODEL,
                messages=[{"role": "user", "content": PLANNER_PROMPT + f"\n\nTopic: {topic}"}],
                tools=None,
                temperature=None,
                timeout_seconds=API_TIMEOUT_SECONDS,
            )

            text = (assistant_msg.get("content") or "").strip()
            if text:
                break  # Got valid response, exit retry loop
            
            # Empty response - retry
            if attempt < max_retries - 1:
                if ui: ui.log(f"Empty response, retrying ({attempt + 2}/{max_retries})...", "WARNING")
                else: console.print(f"[yellow]Empty response from planner, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                time.sleep(2)
            else:
                if ui: ui.log("Planner returned empty response, using defaults", "WARNING")
                else: console.print("[yellow]Planner returned empty response after retries, using defaults[/yellow]")
                return default_plan
                
        except Exception as e:
            if attempt < max_retries - 1:
                if ui: ui.log(f"Planner error: {e}, retrying...", "WARNING")
                else: console.print(f"[yellow]Planner error: {e}, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                time.sleep(2)
            else:
                if ui: ui.log(f"Planner failed: {e}", "ERROR")
                else: console.print(f"[yellow]Planner failed after retries: {e}, using defaults[/yellow]")
                return default_plan
    
    # Extract JSON
    try:
        # Try to find JSON in the response
        if "```json" in text:
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)
        elif "```" in text:
            json_match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)
        
        plan = json.loads(text)
        if ui:
            ui.log("Research plan created", "SUCCESS")
        else:
            console.print("[green]✓ Research plan created[/green]")
        
        # Display plan if NO UI (UI shows status elsewhere)
        if not ui:
            console.print(f"\n[bold]Main Question:[/bold] {plan.get('main_question', 'N/A')}")
            if plan.get('sub_questions'):
                console.print("[bold]Sub-questions:[/bold]")
                for i, q in enumerate(plan['sub_questions'][:5], 1):
                    console.print(f"  {i}. {q}")
        
        return plan
    except json.JSONDecodeError:
        if ui: ui.log("Could not parse plan, using defaults", "WARNING")
        else: console.print("[yellow]Could not parse plan, using defaults[/yellow]")
        return {
            "main_question": topic,
            "sub_questions": [topic],
            "key_concepts": [],
            "expected_sections": ["Introduction", "Background", "Analysis", "Conclusion"],
            "search_queries": [topic]
        }


def create_argument_map(topic: str, research_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Create argument map before research (Phase 0: Argument Dissection)."""
    ui = get_ui()
    if ui:
        ui.log(f"Mapping logical structure for: {topic}", "INFO")
    else:
        console.print(Panel(
            f"[bold magenta]🎯 Argument Dissection[/bold magenta]\n\n"
            f"Mapping logical structure for:\n[white]{topic}[/white]",
            border_style="magenta"
        ))
    
    default_map = {
        "thesis": research_plan.get('main_question', topic),
        "claims": [
            {
                "id": "C1",
                "claim": research_plan.get('main_question', topic),
                "evidence_needed": ["literature review"],
                "counter_arguments": [],
                "dependencies": []
            }
        ]
    }
    
    # Build context from research plan
    context = f"""
TOPIC: {topic}

RESEARCH PLAN:
- Main Question: {research_plan.get('main_question', topic)}
- Sub-questions: {', '.join(research_plan.get('sub_questions', [])[:5])}
- Key Concepts: {', '.join(research_plan.get('key_concepts', [])[:5])}
"""
    
    max_retries = 3
    text = ""
    for attempt in range(max_retries):
        try:
            assistant_msg = llm_chat(
                model=AGENT_MODEL,
                messages=[{"role": "user", "content": ARGUMENT_DISSECTION_PROMPT + context}],
                tools=None,
                temperature=None,
                timeout_seconds=API_TIMEOUT_SECONDS,
            )
            
            text = (assistant_msg.get("content") or "").strip()
            if text:
                break
            
            if attempt < max_retries - 1:
                if ui: ui.log(f"Empty response, retrying ({attempt + 2}/{max_retries})...", "WARNING")
                else: console.print(f"[yellow]Empty response, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                time.sleep(2)
            else:
                if ui: ui.log("Using default argument map", "WARNING")
                else: console.print("[yellow]Using default argument map[/yellow]")
                return default_map
                
        except Exception as e:
            if attempt < max_retries - 1:
                if ui: ui.log(f"Error: {e}, retrying...", "WARNING")
                else: console.print(f"[yellow]Error: {e}, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                time.sleep(2)
            else:
                if ui: ui.log(f"Failed: {e}", "ERROR")
                else: console.print(f"[yellow]Failed: {e}, using defaults[/yellow]")
                return default_map
    
    # Extract JSON
    try:
        if "```json" in text:
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)
        elif "```" in text:
            json_match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)
        
        argument_map = json.loads(text)
        if ui:
            ui.log("Argument map created", "SUCCESS")
        else:
            console.print("[green]✓ Argument map created[/green]")
            
            # Display map
            console.print(f"\n[bold]Thesis:[/bold] {argument_map.get('thesis', 'N/A')}")
            if argument_map.get('claims'):
                console.print(f"[bold]Claims ({len(argument_map['claims'])}):[/bold]")
                for claim in argument_map['claims'][:5]:
                    console.print(f"  {claim['id']}: {claim['claim'][:80]}...")
                    if claim.get('dependencies'):
                        console.print(f"      [dim]Depends on: {', '.join(claim['dependencies'])}[/dim]")
        
        return argument_map
    except json.JSONDecodeError:
        if ui: ui.log("Could not parse argument map, using defaults", "WARNING")
        else: console.print("[yellow]Could not parse argument map, using defaults[/yellow]")
        return default_map
