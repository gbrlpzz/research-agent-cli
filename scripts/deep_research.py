#!/usr/bin/env python3
"""
Deep Research Agent using PaperQA2 (v5) with Gemini.
Performs autonomous multi-step research: search -> evidence -> answer.
"""
import sys
import os
import argparse
import warnings
import logging
import asyncio
from pathlib import Path
from rich.console import Console
from rich.markdown import Markdown
from dotenv import load_dotenv
from datetime import datetime

# Suppress verbose logging
os.environ['LITELLM_LOG'] = 'ERROR'
logging.basicConfig(level=logging.WARNING)
logging.getLogger('paperqa').setLevel(logging.WARNING)

# Suppress warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

# --- Monkey Patch for PaperQA v5 LiteLLMModel ---
try:
    from paperqa import LiteLLMModel
    if not hasattr(LiteLLMModel, 'router'):
        LiteLLMModel.router = property(lambda self: self.get_router())
except ImportError:
    pass
# -----------------------------------------------

console = Console()
load_dotenv()

def setup_agent_settings(library_path):
    """Configure PaperQA2 Settings for Agentic Workflow."""
    try:
        from paperqa import Settings
    except ImportError:
        console.print("[red]Error: paper-qa v5+ is required. Please run: pip install -r requirements.txt[/red]")
        sys.exit(1)

    gemini_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    if not gemini_key:
        console.print("[red]Error: GEMINI_API_KEY not found in .env[/red]")
        sys.exit(1)
    
    os.environ['GEMINI_API_KEY'] = gemini_key

    try:
        import litellm
        litellm.set_verbose = False
    except:
        pass

    # 1. Configure LLMs (Gemini 2.5 Flash)
    llm_model = "gemini/gemini-2.5-flash"
    embedding_model = "gemini/text-embedding-004"
    
    # 2. Configure Environment for Search Tools
    # Set defined email for Crossref to be a 'polite' user
    if 'CROSSREF_MAILTO' not in os.environ:
        os.environ['CROSSREF_MAILTO'] = 'info@gabrielepizzi.com'
    
    # 3. Configure Agent settings
    # Define default tools
    enabled_tools = ["paper_search", "gather_evidence", "gen_answer"]
    
    # Check for paid tools
    exa_key = os.getenv('EXA_API_KEY')
    edison_key = os.getenv('EDISON_API_KEY')
    
    if exa_key or edison_key:
        print("\nFound credentials for paid/external tools:")
        if exa_key: print("- Exa.ai (Semantic Web Search)")
        if edison_key: print("- Edison (Scientific Agent - Costs Credits)")
        
        try:
            choice = input("\nEnable these tools? [y/N] ").lower().strip()
            if choice == 'y':
                # Import and inject tools
                from scripts.agent_tools import exa_search, edison_research
                import paperqa.agents.tools
                
                if exa_key:
                    enabled_tools.append("exa_search")
                    # Inject function into paperqa.agents.tools so it can be found
                    setattr(paperqa.agents.tools, "exa_search", exa_search)
                    print("Enabled: Exa Search")
                    
                if edison_key:
                    enabled_tools.append("edison_research")
                    setattr(paperqa.agents.tools, "edison_research", edison_research)
                    print("Enabled: Edison Research")
                    
        except KeyboardInterrupt:
            print("\nSkipping paid tools.")
            
    agent_settings = {
        "agent_llm": llm_model,
        "agent_type": "ToolSelector",
        "agent_evidence_n": 5,
        "search_count": 10,
        "should_pre_search": True,
        "index_concurrency": 1,
        "timeout": 300.0,
        "tool_names": enabled_tools
    }

    # Initialize Settings with arguments (fields are frozen)
    settings = Settings(
        llm=llm_model,
        summary_llm=llm_model,
        embedding=embedding_model,
        paper_directory=str(library_path),
        index_directory=str(library_path / ".qa_vectordb"),
        agent=agent_settings
    )
    
    return settings

async def run_deep_research(query, library_path):
    """Run the agentic deep research process."""
    import paperqa
    
    settings = setup_agent_settings(library_path)
    
    console.print(f"[bold cyan]Starting Deep Research Agent[/bold cyan]")
    console.print(f"[dim]Query: {query}[/dim]")
    console.print(f"[dim]Backend: Gemini 2.5 Flash[/dim]")
    console.print(f"[dim]Concurrency: 1 (Conservative)[/dim]")
    console.print(f"[dim]Tools: Search (Semantic Scholar, Crossref), Evidence Gathering[/dim]")
    console.print()

    try:
        # Use simple spinner while agent thinks
        with console.status("[bold green]Agent is researching... (this may take a minute)[/bold green]"):
            # agent_query is the main entry point for the autonomous loop
            response = await paperqa.agent_query(
                query=query,
                settings=settings
            )
            
        return response
        
    except Exception as e:
        console.print(f"[bold red]Deep Research Failed:[/bold red] {e}")
        
        # Unpack ExceptionGroup/TaskGroup errors if present (Python 3.11+)
        if hasattr(e, 'exceptions'):
            console.print("\n[bold red]Sub-exceptions:[/bold red]")
            for i, sub_exc in enumerate(e.exceptions, 1):
                console.print(f"{i}. {sub_exc}")
                
        if "rate_limit" in str(e).lower() or "429" in str(e):
             console.print("[yellow]Hint: You hit a rate limit. Try again in a minute.[/yellow]")
             
        sys.exit(1)

def print_response(response):
    """Pretty print the response."""
    console.print("\n[bold green]Research Report[/bold green]")
    console.print("="*60)
    # response.session contains the actual answer data
    console.print(Markdown(response.session.formatted_answer))
    console.print("="*60)
    
    # Sources
    if response.session.contexts:
        console.print("\n[bold yellow]Key Sources Used:[/bold yellow]")
        for ctx in response.session.contexts[:5]: # Top 5 sources
            name = getattr(ctx.text, 'name', 'Unknown')
            creation = getattr(ctx.text, 'year', '')
            console.print(f"- [cyan]{name} ({creation})[/cyan]")
            console.print(f"- [cyan]{name} ({creation})[/cyan]")

def save_report(response, query, output_dir):
    """Save the research report to a markdown file."""
    output_dir.mkdir(exist_ok=True)
    
    # Create filename
    slug = "".join(c if c.isalnum() or c in " -_" else "" for c in query[:50])
    slug = slug.replace(" ", "_").lower()
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{date_str}_{slug}.md"
    filepath = output_dir / filename
    
    content = f"# Deep Research Report\n\n"
    content += f"**Query:** {query}\n"
    content += f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    content += f"---\n\n"
    content += response.session.formatted_answer
    
    if response.session.contexts:
        content += "\n\n## References\n\n"
        for i, ctx in enumerate(response.session.contexts, 1):
             name = getattr(ctx.text, 'name', 'Unknown')
             year = getattr(ctx.text, 'year', 'n.d.')
             content += f"{i}. **{name}** ({year})\n"
             # Add citation if available (bibtex)
             if hasattr(ctx.text, 'doc') and hasattr(ctx.text.doc, 'citation'):
                 content += f"   > {ctx.text.doc.citation}\n"
             content += "\n"

    try:
        filepath.write_text(content)
        console.print(f"\n[bold green]Report saved to:[/bold green] {filepath}")
    except Exception as e:
        console.print(f"[red]Error saving report:[/red] {e}")

def main():
    parser = argparse.ArgumentParser(description="Deep Research Agent with PaperQA2")
    parser.add_argument("query", help="Research topic or question")
    args = parser.parse_args()
    
    # Locate library
    repo_root = Path(__file__).resolve().parent.parent
    library_path = repo_root / "library"
    library_path.mkdir(exist_ok=True)
    
    # Run async main
    try:
        response = asyncio.run(run_deep_research(args.query, library_path))
        print_response(response)
        
        # Save to file
        save_report(response, args.query, repo_root / "reports")
        
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted by user.[/red]")
        sys.exit(0)

if __name__ == "__main__":
    main()
