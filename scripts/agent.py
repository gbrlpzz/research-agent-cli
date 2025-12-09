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

Uses Gemini 3 Pro Preview for planning and writing with function calling
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
from typing import Optional, List, Dict, Any, Set

# Suppress verbose logging
os.environ['LITELLM_LOG'] = 'ERROR'

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from google import genai
from google.genai import types
import logging

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

# Configure Gemini client
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

# Models - using Gemini 3 Pro Preview as specified
AGENT_MODEL = "gemini-3-pro-preview"
FLASH_MODEL = "gemini-2.5-flash"  # For RAG (faster)

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


# ============================================================================
# TOOL FUNCTIONS - Imported from shared tools module
# ============================================================================

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
    export_literature_sheet
)


# ============================================================================
# TOOL DECLARATIONS FOR GEMINI FUNCTION CALLING
# ============================================================================

TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="discover_papers",
            description="Search for papers using Semantic Scholar + paper-scraper. Use FIRST to find papers.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Search query for the topic"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Max results (default 15)")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="exa_search",
            description="Neural search via Exa.ai. COSTS CREDITS - use only when discover_papers isn't enough.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Conceptual search query"),
                    "limit": types.Schema(type=types.Type.INTEGER, description="Max results (default 5)")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="add_paper",
            description="Add a paper to library by arXiv ID or DOI. Downloads PDF and updates master.bib.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "identifier": types.Schema(type=types.Type.STRING, description="arXiv ID or DOI"),
                    "source": types.Schema(type=types.Type.STRING, description="'arxiv', 'doi', or 'auto'")
                },
                required=["identifier"]
            )
        ),
        types.FunctionDeclaration(
            name="query_library",
            description="Ask a research question using PaperQA2 RAG. Uses persistent Qdrant index.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "question": types.Schema(type=types.Type.STRING, description="Research question"),
                    "paper_filter": types.Schema(type=types.Type.STRING, description="Optional keyword filter")
                },
                required=["question"]
            )
        ),
        types.FunctionDeclaration(
            name="fuzzy_cite",
            description="Fuzzy search for @citation_keys. Returns keys that ACTUALLY exist in the library.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(type=types.Type.STRING, description="Author, title, year, or keyword")
                },
                required=["query"]
            )
        ),
        types.FunctionDeclaration(
            name="validate_citations",
            description="Validate citation keys before writing. Use to ensure all @keys exist in library.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "citation_keys": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description="List of citation keys to validate"
                    )
                },
                required=["citation_keys"]
            )
        ),
        types.FunctionDeclaration(
            name="list_library",
            description="List all papers in the library. Check existing papers before adding more.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
                required=[]
            )
        )
    ])
]

TOOL_FUNCTIONS = {
    "discover_papers": discover_papers,
    "exa_search": exa_search,
    "add_paper": add_paper,
    "query_library": query_library,
    "fuzzy_cite": fuzzy_cite,
    "validate_citations": validate_citations,
    "list_library": list_library
}

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are an autonomous research agent that produces academic Typst documents.

You are a RIGOROUS SCHOLAR. Every claim must be grounded. Every citation must be verified.

## Available Tools

### 1. query_library(question, paper_filter)
RAG Q&A using PaperQA2 with persistent Qdrant index.
- START HERE to see what knowledge exists in the library
- Returns detailed answers with citations from indexed papers
- Use first to understand current knowledge, then identify gaps

### 2. list_library()
Show all papers in the library.
- Check what papers are available before searching for more

### 3. discover_papers(query, limit)
Search Semantic Scholar + paper-scraper for papers.
- Use AFTER querying library to fill knowledge gaps
- Returns: title, authors, year, abstract, arxiv_id, doi, citations
- Prioritize high-citation and recent papers

### 4. exa_search(query, limit)  
Neural search via Exa.ai. ⚠️ COSTS CREDITS!
- Only use when discover_papers doesn't find enough
- Good for conceptual/semantic queries

### 5. add_paper(identifier, source)
Download paper to library by arXiv ID or DOI.
- Updates master.bib automatically
- Add 5-10 MOST RELEVANT papers only

### 6. fuzzy_cite(query)
Find @citation_keys for papers in library.
- Fuzzy matches author, title, year
- Returns ONLY keys that actually exist
- ALWAYS use this before citing

### 7. validate_citations(citation_keys)
Validate a list of citation keys before writing.
- Returns valid/invalid keys and suggestions
- Use this to double-check your citations

## Workflow (ITERATIVE - RAG First)

1. **Query First**: query_library() with the main topic to see existing knowledge
2. **Identify Gaps**: Based on the answer, identify what's missing
3. **Search**: discover_papers() for specific gaps
4. **Acquire**: add_paper() for the most relevant papers found
5. **Query Again**: query_library() with more specific questions
6. **Repeat** steps 2-5 until you have comprehensive coverage
7. **Cite**: fuzzy_cite() for EACH paper you want to cite
8. **Validate**: validate_citations() to verify all keys exist
9. **Write**: Output complete Typst document

## Output Format

```typst
#import "lib.typ": project

#show: project.with(
  title: "Your Title",
  subtitle: "A Research Report",
  authors: ("Research Agent",),
  date: "CURRENT_DATE",
  abstract: [
    Concise abstract summarizing topic and findings.
  ]
)

#outline(indent: auto)
#pagebreak()

= Introduction
Content with @citation_key references...

= Background
More content with citations @key1 @key2...

= Analysis
Detailed synthesis...

= Conclusion
Summary and future directions.

#bibliography("refs.bib")
```

## ACADEMIC RIGOR RULES (STRICT)

### Citation Discipline
- NEVER make factual claims without a citation
- ONLY use @citation_keys that were returned by fuzzy_cite()
- If fuzzy_cite() returns no matches, DO NOT cite that paper
- Every paragraph should have at least one citation
- Use multiple citations when synthesizing across sources: @key1 @key2 @key3

### Research Integrity
- Base ALL claims on query_library() responses
- Do NOT invent facts, statistics, or paper claims
- If the library lacks information, explicitly state: "Further research is needed"
- Distinguish between what papers claim vs. what is established fact

### Critical Analysis
- Compare and contrast findings across papers
- Identify limitations and gaps in the literature
- Note methodological differences between studies
- Present multiple perspectives when they exist

### Writing Standards
- Use formal academic tone (no colloquialisms)
- Avoid hedging language unless uncertainty is warranted
- Be precise with terminology
- Define technical terms on first use

### Quality Checklist (verify before outputting)
1. Every factual claim has a citation
2. All @keys were verified via fuzzy_cite()
3. Abstract reflects actual findings from RAG
4. No speculative claims without caveats
5. Limitations are acknowledged"""


# ============================================================================
# AGENT LOOP
# ============================================================================

def run_agent(topic: str, research_plan: Optional[Dict[str, Any]] = None) -> str:
    """Run the research agent on a topic with optional research plan."""
    global _used_citation_keys
    _used_citation_keys = set()  # Reset for new run
    
    # Get current date for the document
    current_date = datetime.now().strftime("%B %Y")
    
    console.print(Panel(
        f"[bold cyan]🤖 Research Agent[/bold cyan]\n\n"
        f"[white]{topic}[/white]\n\n"
        f"[dim]Model: {AGENT_MODEL}[/dim]",
        border_style="cyan"
    ))
    
    # Inject current date into system prompt
    system_prompt_with_date = SYSTEM_PROMPT.replace("CURRENT_DATE", current_date)
    
    # Build user prompt with optional research plan
    plan_section = ""
    if research_plan:
        plan_section = f"""
RESEARCH PLAN:
- Main Question: {research_plan.get('main_question', topic)}
- Sub-questions: {', '.join(research_plan.get('sub_questions', [])[:3])}
- Key Concepts: {', '.join(research_plan.get('key_concepts', [])[:5])}
- Search Queries: {', '.join(research_plan.get('search_queries', [])[:3])}
"""
    
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"""Research this topic and produce a Typst document:

TOPIC: {topic}
{plan_section}
IMPORTANT - Follow the RAG-First workflow:
1. FIRST query_library() with the main topic to see what knowledge already exists
2. Identify gaps in the existing knowledge
3. discover_papers() to find papers addressing those gaps
4. add_paper() for the most relevant papers
5. query_library() again with more specific questions
6. Repeat until comprehensive
7. fuzzy_cite() to get @citation_keys
8. Output complete Typst document (use date: "{current_date}")""")]
        )
    ]
    
    console.print("\n[bold]Starting autonomous research...[/bold]\n")
    
    max_iterations = 35
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        with console.status(f"[cyan]Thinking (step {iteration}/{max_iterations})..."):
            try:
                response = client.models.generate_content(
                    model=AGENT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt_with_date,
                        tools=TOOLS
                    )
                )
            except Exception as e:
                console.print(f"[red]API error: {e}[/red]")
                break
        
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]
            
            if function_calls:
                contents.append(types.Content(role="model", parts=parts))
                
                function_response_parts = []
                for part in function_calls:
                    fc = part.function_call
                    func_name = fc.name
                    func_args = dict(fc.args) if fc.args else {}
                    
                    console.print(f"[yellow]→ {func_name}({json.dumps(func_args, default=str)[:80]})[/yellow]")
                    
                    if func_name in TOOL_FUNCTIONS:
                        try:
                            result = TOOL_FUNCTIONS[func_name](**func_args)
                        except Exception as e:
                            result = {"error": str(e)}
                    else:
                        result = {"error": f"Unknown function: {func_name}"}
                    
                    function_response_parts.append(
                        types.Part(function_response=types.FunctionResponse(
                            name=func_name,
                            response={"result": json.dumps(result, default=str)}
                        ))
                    )
                
                contents.append(types.Content(role="user", parts=function_response_parts))
            else:
                text = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
                
                if "#import" in text and "project.with" in text and "#bibliography" in text:
                    console.print("[green]✓ Document generated[/green]")
                    if "```typst" in text:
                        match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                        if match:
                            return match.group(1).strip()
                    return text
                
                console.print(f"[dim]{text[:150]}...[/dim]")
                
                contents.append(types.Content(role="model", parts=parts))
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text="Continue. Output the complete Typst document when ready.")]
                ))
        else:
            console.print("[yellow]Empty response, retrying...[/yellow]")
    
    return "// Agent did not produce a document within iteration limit"


def filter_bibtex_to_cited(master_bib_path: Path, cited_keys: Set[str]) -> str:
    """Filter master.bib to only include entries that were cited."""
    if not master_bib_path.exists():
        return "% No references\n"
    
    content = master_bib_path.read_text()
    
    # Parse bibtex entries
    pattern = r'(@\w+\{([^,]+),.*?\n\})'
    matches = re.findall(pattern, content, re.DOTALL)
    
    filtered_entries = []
    for full_entry, key in matches:
        key = key.strip()
        if key in cited_keys or not cited_keys:
            filtered_entries.append(full_entry)
    
    if not filtered_entries:
        # Fallback: return all entries if no matches
        return content
    
    return "\n\n".join(filtered_entries) + "\n"


def extract_citations_from_typst(typst_content: str) -> Set[str]:
    """Extract all @citation_key references from Typst content."""
    # Match @key patterns (not in code blocks)
    pattern = r'@([a-zA-Z][a-zA-Z0-9_-]*)'
    matches = re.findall(pattern, typst_content)
    return set(matches)


# ============================================================================
# RESEARCH PLANNER
# ============================================================================

PLANNER_PROMPT = """You are a research planning assistant. Given a topic, create a structured research plan.

Output a JSON research plan with:
1. "main_question": The central research question
2. "sub_questions": 3-5 specific questions to investigate
3. "key_concepts": Important terms/concepts to search for
4. "expected_sections": Proposed document structure
5. "search_queries": 3-4 specific search queries for academic databases

Be specific and academic. Output ONLY valid JSON, no markdown."""

def create_research_plan(topic: str) -> Dict[str, Any]:
    """Create a structured research plan before starting research."""
    console.print(Panel(
        f"[bold blue]📋 Research Planner[/bold blue]\n\n"
        f"Creating research plan for:\n[white]{topic}[/white]",
        border_style="blue"
    ))
    
    response = client.models.generate_content(
        model=AGENT_MODEL,
        contents=[types.Content(
            role="user",
            parts=[types.Part(text=f"Create a research plan for: {topic}")]
        )],
        config=types.GenerateContentConfig(
            system_instruction=PLANNER_PROMPT
        )
    )
    
    text = response.candidates[0].content.parts[0].text
    
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
        console.print("[green]✓ Research plan created[/green]")
        
        # Display plan
        console.print(f"\n[bold]Main Question:[/bold] {plan.get('main_question', 'N/A')}")
        if plan.get('sub_questions'):
            console.print("[bold]Sub-questions:[/bold]")
            for i, q in enumerate(plan['sub_questions'][:5], 1):
                console.print(f"  {i}. {q}")
        
        return plan
    except json.JSONDecodeError:
        console.print("[yellow]Could not parse plan, using defaults[/yellow]")
        return {
            "main_question": topic,
            "sub_questions": [topic],
            "key_concepts": [],
            "expected_sections": ["Introduction", "Background", "Analysis", "Conclusion"],
            "search_queries": [topic]
        }


# ============================================================================
# PEER REVIEWER
# ============================================================================

REVIEWER_PROMPT = """You are a rigorous academic peer reviewer. You review research documents with strict standards.

Your review must assess:

## 1. SCIENTIFIC RIGOR
- Are all claims properly supported by citations?
- Is the methodology (if applicable) sound?
- Are limitations acknowledged?

## 2. ARGUMENTATION
- Is the thesis clear and well-supported?
- Is the logical flow coherent?
- Are counter-arguments addressed?

## 3. CITATION QUALITY
- Are citations appropriate and sufficient?
- Are primary sources used where needed?
- Is there over-reliance on any single source?

## 4. COMPLETENESS
- Are all major aspects of the topic covered?
- Are there obvious gaps in the literature review?
- Is the scope appropriate?

## 5. WRITING QUALITY
- Is the academic tone consistent?
- Is technical terminology used correctly?
- Is the structure clear?

Output a STRUCTURED REVIEW with:
1. **Overall Assessment**: Accept / Minor Revisions / Major Revisions / Reject
2. **Strengths**: 2-3 bullet points
3. **Critical Issues**: Specific problems that MUST be fixed
4. **Suggestions**: Optional improvements
5. **Missing Content**: Topics/sources that should be added
6. **Specific Line Edits**: Concrete changes needed

Be constructive but rigorous. Academic excellence is the standard."""


def peer_review(
    document: str,
    topic: str,
    revision_num: int = 1,
    research_plan: Optional[Dict[str, Any]] = None,
    refs_bib: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run peer review on a document. Returns structured feedback.
    
    Now includes full context: research plan and bibliography for verification.
    """
    console.print(Panel(
        f"[bold magenta]🔍 Peer Review (Round {revision_num})[/bold magenta]\n\n"
        f"Reviewing document on: [white]{topic[:50]}...[/white]",
        border_style="magenta"
    ))
    
    # Build context section
    context_parts = []
    
    if research_plan:
        context_parts.append(f"""
RESEARCH PLAN:
- Main Question: {research_plan.get('main_question', 'N/A')}
- Sub-questions: {', '.join(research_plan.get('sub_questions', [])[:3])}
- Key Concepts: {', '.join(research_plan.get('key_concepts', [])[:5])}
""")
    
    if refs_bib:
        # Extract citation keys from bib for verification
        import re
        bib_keys = re.findall(r'@\w+\{([^,]+),', refs_bib)
        context_parts.append(f"""
AVAILABLE BIBLIOGRAPHY ({len(bib_keys)} entries):
Citation keys: {', '.join(bib_keys[:15])}{'...' if len(bib_keys) > 15 else ''}

You can verify that all @citations in the document match actual bibliography entries.
""")
    
    context_section = "\n".join(context_parts) if context_parts else ""
    
    response = client.models.generate_content(
        model=AGENT_MODEL,
        contents=[types.Content(
            role="user",
            parts=[types.Part(text=f"""Review this academic document:

TOPIC: {topic}
{context_section}
DOCUMENT:
```typst
{document}
```

Provide a structured peer review following your guidelines.
Pay special attention to:
1. Whether all claims are properly supported by citations
2. Whether cited keys match the available bibliography
3. Whether the research plan questions are adequately addressed""")]
        )],
        config=types.GenerateContentConfig(
            system_instruction=REVIEWER_PROMPT
        )
    )
    
    # Defensive handling for API response
    try:
        if not response.candidates or not response.candidates[0].content.parts:
            log_debug("Peer review: empty response from API")
            review_text = "**Review unavailable** - The API returned an empty response. Please retry."
        else:
            review_text = response.candidates[0].content.parts[0].text
    except (IndexError, AttributeError, TypeError) as e:
        log_debug(f"Peer review response error: {e}")
        review_text = f"**Review unavailable** - Error parsing response: {e}"
    
    # Determine verdict
    verdict = "minor_revisions"
    if "Accept" in review_text and "Major" not in review_text:
        verdict = "accept"
    elif "Major Revisions" in review_text or "Major revisions" in review_text:
        verdict = "major_revisions"
    elif "Reject" in review_text:
        verdict = "reject"
    
    console.print(f"[bold]Verdict: {verdict.upper()}[/bold]")
    
    # Show summary
    lines = review_text.split('\n')[:15]
    for line in lines:
        if line.strip():
            console.print(f"[dim]{line}[/dim]")
    
    return {
        "verdict": verdict,
        "review": review_text,
        "revision_number": revision_num
    }


# ============================================================================
# REVISION AGENT
# ============================================================================

REVISION_PROMPT = """You are revising an academic document based on peer review feedback.

Your task:
1. Read the ORIGINAL document carefully
2. Study the PEER REVIEW feedback
3. Create an IMPLEMENTATION PLAN addressing each issue
4. Output the REVISED document

Address ALL critical issues raised. Maintain academic rigor.
Keep all existing citations but add more where needed.
Output the complete revised Typst document."""


def revise_document(original: str, review: str, topic: str, research_plan: Dict) -> str:
    """Revise document based on peer review feedback."""
    console.print(Panel(
        f"[bold yellow]✏️ Revision Phase[/bold yellow]\n\n"
        f"Addressing peer review feedback...",
        border_style="yellow"
    ))
    
    current_date = datetime.now().strftime("%B %Y")
    
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"""Revise this document based on peer review.

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
""")]
        )
    ]
    
    # Run revision loop
    for iteration in range(15):
        with console.status(f"[cyan]Revising (step {iteration + 1})..."):
            try:
                response = client.models.generate_content(
                    model=AGENT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=REVISION_PROMPT,
                        tools=TOOLS
                    )
                )
            except Exception as e:
                console.print(f"[red]API error: {e}[/red]")
                return original
        
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]
            
            if function_calls:
                contents.append(types.Content(role="model", parts=parts))
                
                function_response_parts = []
                for part in function_calls:
                    fc = part.function_call
                    func_name = fc.name
                    func_args = dict(fc.args) if fc.args else {}
                    
                    console.print(f"[yellow]→ {func_name}({json.dumps(func_args, default=str)[:60]})[/yellow]")
                    
                    if func_name in TOOL_FUNCTIONS:
                        try:
                            result = TOOL_FUNCTIONS[func_name](**func_args)
                        except Exception as e:
                            result = {"error": str(e)}
                    else:
                        result = {"error": f"Unknown function: {func_name}"}
                    
                    function_response_parts.append(
                        types.Part(function_response=types.FunctionResponse(
                            name=func_name,
                            response={"result": json.dumps(result, default=str)}
                        ))
                    )
                
                contents.append(types.Content(role="user", parts=function_response_parts))
            else:
                text = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
                
                if "#import" in text and "project.with" in text:
                    console.print("[green]✓ Revision complete[/green]")
                    if "```typst" in text:
                        match = re.search(r'```typst\s*(.*?)\s*```', text, re.DOTALL)
                        if match:
                            return match.group(1).strip()
                    return text
                
                contents.append(types.Content(role="model", parts=parts))
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text="Continue. Output the complete revised Typst document.")]
                ))
    
    return original  # Fallback


# ============================================================================
# MULTI-PHASE ORCHESTRATOR
# ============================================================================

def generate_report(topic: str, max_revisions: int = 3) -> Path:
    """Generate a research report with planning, review, and revision phases."""
    global _used_citation_keys, _debug_logger
    _used_citation_keys = set()
    
    REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    
    # Create report directory early
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    topic_slug = "".join(c if c.isalnum() or c == " " else "" for c in topic[:40])
    topic_slug = topic_slug.strip().replace(" ", "_").lower()
    report_name = f"{timestamp}_{topic_slug}"
    report_dir = REPORTS_PATH / report_name
    report_dir.mkdir(parents=True, exist_ok=True)
    
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
    
    # Save plan to artifacts
    (artifacts_dir / "research_plan.json").write_text(json.dumps(research_plan, indent=2))
    log_debug(f"Research plan created: {json.dumps(research_plan)}")
    
    # ========== PHASE 2: RESEARCH & WRITE ==========
    console.print(Panel(
        f"[bold cyan]Phase 2: Research & Writing[/bold cyan]",
        border_style="cyan", width=60
    ))
    
    typst_content = run_agent(topic, research_plan=research_plan)
    
    # Save initial draft with its refs.bib
    (artifacts_dir / "draft_initial.typ").write_text(typst_content)
    
    # Generate refs.bib for the initial draft
    doc_citations = extract_citations_from_typst(typst_content)
    all_cited = get_used_citation_keys() | doc_citations
    if MASTER_BIB.exists():
        current_refs_bib = filter_bibtex_to_cited(MASTER_BIB, all_cited)
    else:
        current_refs_bib = "% No references\n"
    (artifacts_dir / "draft_initial_refs.bib").write_text(current_refs_bib)
    
    log_debug(f"Initial draft complete with {len(all_cited)} citations")
    
    # ========== PHASE 3: PEER REVIEW LOOP ==========
    reviews = []
    
    for revision_round in range(1, max_revisions + 1):
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
        review_result = peer_review(
            typst_content,
            topic,
            revision_round,
            research_plan=research_plan,
            refs_bib=current_refs_bib
        )
        reviews.append(review_result)
        
        # Save review to artifacts
        review_file = artifacts_dir / f"peer_review_r{revision_round}.md"
        review_file.write_text(f"# Peer Review - Round {revision_round}\n\n**Verdict**: {review_result['verdict'].upper()}\n\n{review_result['review']}")
        log_debug(f"Review {revision_round}: {review_result['verdict']}")
        
        # Check if accepted
        if review_result['verdict'] == 'accept':
            console.print("[bold green]✓ Paper accepted by reviewer![/bold green]")
            break
        
        # ========== PHASE 4: REVISION ==========
        console.print(Panel(
            f"[bold yellow]Phase 4.{revision_round}: Revision[/bold yellow]",
            border_style="yellow", width=60
        ))
        
        typst_content = revise_document(
            typst_content, 
            review_result['review'], 
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
    
    # Write final main.typ
    main_typ = report_dir / "main.typ"
    main_typ.write_text(typst_content)
    
    # Compile to PDF
    with console.status("[dim]Compiling PDF..."):
        try:
            result = subprocess.run(
                ["typst", "compile", "main.typ"],
                cwd=report_dir,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                log_debug(f"Typst error: {result.stderr}")
        except FileNotFoundError:
            log_debug("typst not found")
        except Exception as e:
            log_debug(f"Compile error: {e}")
    
    # Export literature review sheet
    literature_sheet = export_literature_sheet()
    (report_dir / "literature_sheet.md").write_text(literature_sheet)
    log_debug(f"Literature sheet exported with {len(get_reviewed_papers())} papers")
    
    # Summary
    reviewed_count = len(get_reviewed_papers())
    cited_count = sum(1 for p in get_reviewed_papers().values() if p.get('cited'))
    
    console.print("\n" + "="*60)
    console.print(Panel(
        f"[bold green]✓ Research Complete[/bold green]\n\n"
        f"[white]Topic:[/white] {topic[:50]}...\n"
        f"[white]Reviews:[/white] {len(reviews)} rounds\n"
        f"[white]Final verdict:[/white] {reviews[-1]['verdict'].upper() if reviews else 'N/A'}\n"
        f"[white]Papers:[/white] {cited_count} cited / {reviewed_count} reviewed\n\n"
        f"[dim]Output:[/dim]\n"
        f"  📝 main.typ\n"
        f"  📄 main.pdf\n"
        f"  📚 refs.bib\n"
        f"  📊 literature_sheet.md\n"
        f"  📁 artifacts/ (plans, drafts, reviews)\n\n"
        f"[dim]{report_dir}[/dim]",
        border_style="green"
    ))
    
    log_debug(f"Session complete: {report_dir}")
    return report_dir


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

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
        """
    )
    parser.add_argument('topic', nargs='+', help='Research topic')
    parser.add_argument('--revisions', '-r', type=int, default=3,
                       help='Max peer review rounds (default: 3)')
    
    args = parser.parse_args()
    topic = " ".join(args.topic)
    
    if not topic:
        parser.print_help()
        sys.exit(1)
    
    if not os.getenv('GEMINI_API_KEY'):
        console.print("[red]Error: GEMINI_API_KEY not set[/red]")
        sys.exit(1)
    
    generate_report(topic, max_revisions=args.revisions)
