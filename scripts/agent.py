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
import time
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


# ============================================================================
# TOOL FUNCTIONS - Imported from shared tools module
# ============================================================================

from tools import (
    discover_papers,
    exa_search, 
    add_paper,
    batch_add_papers,
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
            name="batch_add_papers",
            description="Add multiple papers to library in parallel. Much faster than sequential add_paper calls.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "identifiers": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "identifier": types.Schema(type=types.Type.STRING, description="arXiv ID or DOI"),
                                "source": types.Schema(type=types.Type.STRING, description="'arxiv', 'doi', or 'auto'")
                            },
                            required=["identifier"]
                        ),
                        description="List of papers to add"
                    ),
                    "max_workers": types.Schema(type=types.Type.INTEGER, description="Max parallel downloads (default 3)")
                },
                required=["identifiers"]
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

# Tools available to the REVIEWER (Read-only / Discovery)
# Tools available to the REVIEWER (Read-only / Discovery)
# Create a new Tool object with only the allowed function declarations
REVIEWER_TOOLS = [
    types.Tool(function_declarations=[
        fd for fd in TOOLS[0].function_declarations
        if fd.name in ["query_library", "fuzzy_cite", "validate_citations", "list_library", "discover_papers"]
    ])
]

TOOL_FUNCTIONS = {
    "discover_papers": discover_papers,
    "exa_search": exa_search,
    "add_paper": add_paper,
    "batch_add_papers": batch_add_papers,
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

### 6. batch_add_papers(identifiers, max_workers)
⚡ Add MULTIPLE papers in parallel (3x faster than sequential add_paper).
- Use when you need to add 3+ papers at once
- Greatly speeds up initial research phase
- Example: batch_add_papers(identifiers=[{"identifier": "10.1234/abc", "source": "doi"}, {"identifier": "2301.12345", "source": "arxiv"}])

### 7. fuzzy_cite(query)
Find @citation_keys for papers in library.
- Fuzzy matches author, title, year
- Returns ONLY keys that actually exist
- ALWAYS use this before citing

### 7. validate_citations(citation_keys)
Validate a list of citation keys before writing.
- Returns valid/invalid keys and suggestions
- Use this to double-check your citations

## Research Workflow (Library-First, Gap-Driven)

PHASE 1: LIBRARY SCAN
- list_library() to see available papers
- query_library() for EACH sub-question from research plan
- Identify which questions have good coverage vs gaps

PHASE 2: TARGETED DISCOVERY (gaps only)
- discover_papers() for questions that lack library coverage
- add_paper() or batch_add_papers() for 3-5 most relevant papers per gap
- query_library() again to verify new papers fill the gap

PHASE 3: EVIDENCE SYNTHESIS
- For each planned section, query_library() to gather evidence
- Build argument structure with citations from RAG answers
- fuzzy_cite() for EVERY paper you want to cite
- Keep track of ALL @keys returned by fuzzy_cite()

PHASE 4: WRITE DRAFT
- Write Typst document using ONLY @keys from fuzzy_cite()
- Every claim must have a citation from fuzzy_cite()
- If a key wasn't returned by fuzzy_cite(), DO NOT USE IT

🚨 PHASE 5: VALIDATE BEFORE FINALIZING (CRITICAL!)
BEFORE outputting the final document, you MUST:
1. Extract ALL @citation_keys from your draft
2. Call validate_citations(citation_keys) with the complete list
3. If ANY keys are invalid:
   - Call discover_papers() to find the missing papers
   - Call add_paper() to add them to library
   - Call fuzzy_cite() to get the CORRECT citation keys
   - Replace the invalid keys with the correct ones
4. ONLY output the document after ALL citations are validated

IMPORTANT: Do NOT discover papers randomly. Query library FIRST to avoid wasting time on papers you already have.

## Document Length Guidelines

You are FREE to write as comprehensively as needed to do justice to the topic.

**Length Expectations:**
- **Simple topics**: 2-3 pages minimum (don't rush)
- **Complex topics**: 10-20 pages encouraged (be thorough)
- **NEVER sacrifice depth for brevity**

**Structural Freedom:**
- Include ALL relevant sections (Introduction, Background, Methodology, Analysis, Discussion, Related Work, Limitations, Conclusion)
- Use subsections liberally to organize complex arguments
- Add figures/tables if they clarify concepts (wrap in Typst figure blocks)
- Develop arguments fully with proper evidence and citations

**Quality > Brevity:**
- The document should be PUBLICATION-READY, not a summary
- Each claim needs proper support and citations
- Complex topics deserve complex treatment
- Don't stop writing because you've reached some arbitrary length

## Cover Page Formatting Rules
1. **Title**: MUST be very short (max 7 words).
2. **Subtitle**: EVERYTHING after the colon (:) in the topic MUST go here. If no colon, write a descriptive subtitle.
3. **Date**: Use the fixed date "December 09, 2025".
4. **Abstract**: Must be included in the `#show: project.with(...)` call.

## Output Format

```typst
#import "lib.typ": project

#show: project.with(
  title: "Short Main Title",
  subtitle: "Everything after the colon goes here",
  authors: ("Research Agent",),
  date: "December 09, 2025", 
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

## Typst Formatting Rules (NOT Markdown!)

CRITICAL: Typst is NOT Markdown. Use these formats:

| Element | Typst Syntax | WRONG (Markdown) |
|---------|--------------|------------------|
| Bold | *text* | **text** |
| Italic | _text_ | *text* |
| Heading 1 | = Title | # Title |
| Heading 2 | == Section | ## Section |
| Bullet list | - item | - item (same) |
| Numbered list | + item | 1. item |
| Citation | @citation_key | [@key] |
| Code | `code` | `code` (same) |
| Block quote | #quote[text] | > text |

NEVER use ** for bold - this causes compilation errors!

## ACADEMIC RIGOR RULES (STRICT)

### Citation Density (Academic Standard)
**Every paragraph MUST have 2-3 citations minimum.**
- Introductory/background paragraphs: 2-4 citations
- Claims/arguments: 3-5 citations (synthesize multiple sources)
- Single-source paragraphs are UNACCEPTABLE unless direct quotation
- Aim for 15-25 total citations in a comprehensive document
- **Under-cited documents will be rejected by reviewers**

### Citation Discipline
- NEVER make factual claims without a citation
- ONLY use @citation_keys that were returned by fuzzy_cite()
- Multiple citations for same claim: @key1 @key2 @key3
- BEFORE finalizing, run validate_citations() and fix any invalid keys

### Punctuation Rules (Critical for Copy-Paste Compatibility)
**Use ASCII punctuation ONLY - no Unicode characters:**
- NEVER use em-dashes (—) - rewrite sentences or use commas/periods instead
- Use regular quotes `"text"` instead of curly quotes
- Use three dots `...` instead of ellipsis (…)
- Use `'` (straight apostrophe) instead of curly apostrophe

**Why**: Unicode punctuation renders as escape codes (u2014, u201C) when copied from PDFs.

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
    
    max_iterations = MAX_AGENT_ITERATIONS
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Update system prompt with latest available citations (Dynamic Injection)
        current_papers = get_reviewed_papers()
        citation_section = ""
        if current_papers:
            citation_list = []
            for key, data in current_papers.items():
                citation_list.append(f"- @{key}: \"{data.get('title', 'Unknown')}\" ({data.get('year', '')})")
            
            citation_section = "\n\n## AVAILABLE CITATIONS (Use ONLY these exact keys):\n" + "\n".join(citation_list[:50]) # Limit to 50 to save context
            
        current_system_prompt = system_prompt_with_date + citation_section
        
        with console.status(f"[cyan]Thinking (step {iteration}/{max_iterations})..."):
            try:
                response = client.models.generate_content(
                    model=AGENT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=current_system_prompt,
                        response_modalities=["TEXT"],  # Add this
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


def fix_typst_error(typst_path: Path, error_msg: str):
    """Attempt to fix common Typst errors."""
    content = typst_path.read_text()
    original_content = content
    
    # Fix 1: Double asterisks (Markdown bold) -> Single asterisk (Typst bold)
    if "**" in content:
        content = content.replace("**", "*")
        
    # Fix 2: Unclosed delimiters (often due to mismatched *)
    # Simple heuristic: if odd number of *, remove the last one? 
    # Or just remove all * if it's failing hard? 
    # For now, let's try to close it if it's "unclosed delimiter"
    if "unclosed delimiter" in error_msg:
        # Check for odd number of *
        if content.count("*") % 2 != 0:
            # Try to find a paragraph with odd * and close it? 
            # Too complex. Let's just strip formatting from the likely problematic line?
            # Or just append a * to the end?
            pass
            
    # Fix 3: Invalid references (e.g., @key with special chars)
    # Typst only allows letters, numbers, _, - in labels
    # If error is "label does not exist", we handled that via refs.bib generation, 
    # but maybe the key format is wrong in the typ file?
    
    if content != original_content:
        typst_path.write_text(content)
        return True
    return False


def compile_and_fix(typ_path: Path, max_attempts: int = 3) -> bool:
    """Compile Typst file and attempt to auto-fix errors."""
    pdf_path = typ_path.with_suffix(".pdf")
    
    for attempt in range(max_attempts):
        result = subprocess.run(
            ["typst", "compile", str(typ_path.name), str(pdf_path.name)],
            cwd=str(typ_path.parent),
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            console.print(f"[green]✓ Compiled {typ_path.name}[/green]")
            return True
            
        console.print(f"[yellow]⚠ Compile validation failed (attempt {attempt+1}): {result.stderr.strip()[:100]}[/yellow]")
        
        # Try to fix
        if attempt < max_attempts - 1:
            if fix_typst_error(typ_path, result.stderr):
                console.print("[cyan]  Applying auto-fix...[/cyan]")
                continue
    
    console.print(f"[red]❌ Failed to compile {typ_path.name} after {max_attempts} attempts[/red]")
    return False


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
    
    default_plan = {
        "main_question": topic,
        "sub_questions": [topic],
        "key_concepts": [],
        "expected_sections": ["Introduction", "Analysis", "Discussion", "Conclusion"],
        "search_queries": [topic]
    }
    
    # Retry up to 3 times for API issues
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=AGENT_MODEL,
                contents=[
                    types.Content(role="user", parts=[types.Part(text=PLANNER_PROMPT + f"\n\nTopic: {topic}")])
                ],
                config=types.GenerateContentConfig(
                    temperature=0.7,
                )
            )
            
            # Check for valid response
            if response.candidates and response.candidates[0].content.parts:
                text = response.candidates[0].content.parts[0].text
                if text and text.strip():
                    break  # Got valid response, exit retry loop
            
            # Empty response - retry
            if attempt < max_retries - 1:
                console.print(f"[yellow]Empty response from planner, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                time.sleep(2)  # Brief delay before retry
            else:
                console.print("[yellow]Planner returned empty response after retries, using defaults[/yellow]")
                return default_plan
                
        except Exception as e:
            if attempt < max_retries - 1:
                console.print(f"[yellow]Planner error: {e}, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                time.sleep(2)
            else:
                console.print(f"[yellow]Planner failed after retries: {e}, using defaults[/yellow]")
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

REVIEWER_PROMPT = """You are a rigorous academic peer reviewer.
You are reviewing a Typst document on: "{topic}"

Your goal is to ensure the paper meets high academic standards.
You have access to tools to VERIFY claims and citations.

## Review Process
1.  **Validate Citations**: Use `validate_citations` to check if all @keys exist in the library.
2.  **Verify Claims**: Use `query_library` to check if specific claims are supported by the cited papers.
3.  **Check Literature Coverage**: Use `discover_papers` to find missing key references.
4.  **Recommend Improvements**: Identify weak arguments, missing citations, or hallucinations.

## Previous Reviews
If provided, check if the author has addressed the following feedback from previous rounds:
{previous_reviews}

## Output Format

Write your review as natural text organized in these sections:

**VERDICT**: [Accept | Minor Revisions | Major Revisions | Reject]

**SUMMARY**: 
Brief assessment of the paper's quality and main contributions.

**STRENGTHS**:
- Strength 1
- Strength 2

**WEAKNESSES**:
- Weakness 1
- Weakness 2

**RECOMMENDED PAPERS** (if any missing key works):
For each recommended paper, write ONE line in this exact format:
RECOMMEND DOI: 10.xxxx/yyyy | Reason: Why this paper is needed
OR
RECOMMEND SEARCH: "search query terms" | Reason: Why these papers are needed

**SPECIFIC EDITS** (if needed):
Section: Introduction
Issue: Claim lacks citation
Suggestion: Add citation from Smith 2020

Be constructive but rigorous. Academic excellence is the standard.
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

    # Initialize tool-enabled chat with proper conversation history
    contents = [
        types.Content(
             role="user",
             parts=[types.Part(text=context + "\n\n" + user_msg)]
        )
    ]
    
    # Reviewer loop (configurable iteration limit to prevent infinite loops)
    max_steps = MAX_REVIEWER_ITERATIONS
    step = 0
    final_review = None
    
    while step < max_steps:
        step += 1
        try:
            response = client.models.generate_content(
                model=AGENT_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=REVIEWER_PROMPT.format(topic=topic, previous_reviews=previous_reviews),
                    tools=REVIEWER_TOOLS,  # Restricted toolset
                    temperature=0.3  # Low temperature for rigorous checking
                )
            )
        except Exception as e:
            log_debug(f"Reviewer API error: {e}")
            break
            
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]
            
            if function_calls:
                # Execute tools (verification steps)
                contents.append(types.Content(role="model", parts=parts))
                function_response_parts = []
                
                for part in function_calls:
                    fc = part.function_call
                    func_name = fc.name
                    func_args = dict(fc.args) if fc.args else {}
                    
                    console.print(f"[magenta]  Reviewer: {func_name}(...)[/magenta]")
                    
                    if func_name in TOOL_FUNCTIONS:
                        try:
                            # Limited toolset for reviewer? For now give all.
                            result = TOOL_FUNCTIONS[func_name](**func_args)
                        except Exception as e:
                            result = {"error": str(e)}
                    else:
                        result = {"error": "Unknown function"}
                        
                    function_response_parts.append(
                        types.Part(function_response=types.FunctionResponse(
                            name=func_name,
                            response={"result": json.dumps(result, default=str)}
                        ))
                    )
                contents.append(types.Content(role="user", parts=function_response_parts))
            else:
                # Text response - the review in plain text format
                text = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
                
                # Parse plain text review
                if "**VERDICT**" in text or "VERDICT:" in text:
                    # Extract verdict
                    verdict_match = re.search(r'\*\*VERDICT\*\*:\s*\[?([^\]\n]+)', text) or re.search(r'VERDICT:\s*\[?([^\]\n]+)', text)
                    verdict = verdict_match.group(1).strip().lower() if verdict_match else "minor_revisions"
                    if "accept" in verdict:
                        verdict = "accept"
                    elif "major" in verdict:
                        verdict = "major_revisions"
                    else:
                        verdict = "minor_revisions"
                    
                    # Extract summary
                    summary_match = re.search(r'\*\*SUMMARY\*\*:?\s*\n(.*?)(?=\n\*\*|$)', text, re.DOTALL)
                    summary = summary_match.group(1).strip() if summary_match else text[:200]
                    
                    # Extract weaknesses (for context)
                    weaknesses_match = re.search(r'\*\*WEAKNESSES\*\*:?\s*\n(.*?)(?=\n\*\*|$)', text, re.DOTALL)
                    weaknesses = weaknesses_match.group(1).strip() if weaknesses_match else ""
                    
                    # Extract recommended papers
                    recommendations = []
                    for line in text.split('\n'):
                        if "RECOMMEND DOI:" in line:
                            # Format: RECOMMEND DOI: 10.xxxx/yyyy | Reason: ...
                            doi_match = re.search(r'RECOMMEND DOI:\s*(10\.\S+)', line)
                            reason_match = re.search(r'Reason:\s*(.+)', line)
                            if doi_match:
                                recommendations.append({
                                    "doi": doi_match.group(1).strip(),
                                    "reason": reason_match.group(1).strip() if reason_match else "Recommended by reviewer"
                                })
                        elif "RECOMMEND SEARCH:" in line:
                            # Format: RECOMMEND SEARCH: "query" | Reason: ...
                            query_match = re.search(r'RECOMMEND SEARCH:\s*["\']([^"\']+)["\']', line)
                            reason_match = re.search(r'Reason:\s*(.+)', line)
                            if query_match:
                                recommendations.append({
                                    "query": query_match.group(1).strip(),
                                    "reason": reason_match.group(1).strip() if reason_match else "Recommended by reviewer"
                                })
                    
                    final_review = {
                        "verdict": verdict,
                        "summary": summary,
                        "weaknesses": weaknesses,
                        "recommended_papers": recommendations,
                        "full_text": text
                    }
                    console.print(f"[bold]Reviewer #{reviewer_id} Verdict: {verdict.upper()}[/bold]")
                    break  # Done!
                
                # If not done, append and continue
                contents.append(types.Content(role="model", parts=parts))
                if step == max_steps - 1:
                    contents.append(types.Content(role="user", parts=[types.Part(text="Please provide your final review now.")]))

    if not final_review:
        # Fallback
        final_review = {
            "verdict": "minor_revisions",
            "summary": "Reviewer did not produce structured review",
            "weaknesses": "",
            "recommended_papers": [],
            "full_text": ""
        }
        
    return {
        "reviewer_id": reviewer_id,
        "round": round_num,
        **final_review
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
                        tools=TOOLS,
                        timeout=API_TIMEOUT_SECONDS  # Safety timeout
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
    
    # ========== PHASE 2: RESEARCH & WRITE ==========
    console.print(Panel(
        f"[bold cyan]Phase 2: Research & Writing[/bold cyan]",
        border_style="cyan", width=60
    ))
    
    typst_content = run_agent(topic, research_plan=research_plan)
    
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
    (report_dir / "literature_sheet.csv").write_text(literature_sheet)
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
    parser.add_argument('--reviewers', type=int, default=1,
                       help='Number of parallel reviewers (default: 1)')
    
    args = parser.parse_args()
    topic = " ".join(args.topic)
    
    if not topic:
        parser.print_help()
        sys.exit(1)
    
    if not os.getenv('GEMINI_API_KEY'):
        console.print("[red]Error: GEMINI_API_KEY not set[/red]")
        sys.exit(1)
    
    generate_report(topic, max_revisions=args.revisions, num_reviewers=args.reviewers)
