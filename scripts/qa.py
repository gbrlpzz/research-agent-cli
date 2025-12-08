#!/usr/bin/env python3
"""
Question-answering system using paper-qa with Gemini.
Queries all PDFs in the local library to answer questions with citations.
Supports subset filtering, answer export, and interactive chat.
"""
import sys
import os
import argparse
import warnings
from pathlib import Path
from datetime import datetime

# Suppress LiteLLM verbose logging BEFORE any imports that use it
os.environ['LITELLM_LOG'] = 'ERROR'

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.markdown import Markdown
from dotenv import load_dotenv
import logging

# Setup
console = Console()
load_dotenv()

# Suppress litellm verbose output
try:
    import litellm
    litellm.suppress_debug_info = True
    litellm.set_verbose = False
except:
    pass

# Logging to file only (not console)
logging.basicConfig(
    filename='debug_research.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Suppress paper-qa's verbose logging to console
logging.getLogger('paperqa').setLevel(logging.WARNING)
logging.getLogger('litellm').setLevel(logging.ERROR)

# Suppress deprecation warnings from paperqa
warnings.filterwarnings('ignore', category=DeprecationWarning, module='paperqa')
warnings.filterwarnings('ignore', message='.*synchronous.*deprecated.*')
warnings.filterwarnings('ignore', message='coroutine.*was never awaited')

def setup_gemini_settings():
    """Configure paper-qa to use Gemini."""
    from paperqa import Settings
    
    # Check for Gemini API key
    gemini_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    if not gemini_key:
        console.print("[bold red]Error:[/bold red] GEMINI_API_KEY not found in .env")
        console.print("\n[yellow]Please add your Gemini API key to .env:[/yellow]")
        console.print("  GEMINI_API_KEY=your_key_here")
        console.print("\n[dim]Get a free key at: https://makersuite.google.com/app/apikey[/dim]")
        sys.exit(1)
    
    # Set environment variable for litellm
    os.environ['GEMINI_API_KEY'] = gemini_key
    
    # Ensure litellm stays quiet
    try:
        import litellm
        litellm.set_verbose = False
    except:
        pass
    
    # Configure settings for Gemini
    settings = Settings()
    settings.llm = "gemini/gemini-2.5-flash"  # Gemini 2.5 Flash
    settings.summary_llm = "gemini/gemini-2.5-flash"
    settings.embedding = "gemini/text-embedding-004"  # Use Gemini embeddings (free) instead of OpenAI
    settings.answer.answer_max_sources = 5
    settings.answer.evidence_k = 10
    
    logging.info(f"Configured paper-qa with Gemini: {settings.llm}")
    return settings


def answer_question(question, library_path, filter_pattern=None):
    """Answer a question using papers in the library.
    
    Args:
        question: Question to answer
        library_path: Path to library directory
        filter_pattern: Optional pattern to filter papers (e.g., 'vaswani' or 'attention')
    """
    from paperqa import Docs
    
    # Setup settings
    settings = setup_gemini_settings()
    
    # Create Docs object
    docs = Docs()
    
    # Find all PDFs in library
    all_pdf_files = list(library_path.rglob("*.pdf"))
    
    # Filter PDFs if pattern provided
    if filter_pattern:
        pdf_files = []
        pattern_lower = filter_pattern.lower()
        for pdf in all_pdf_files:
            # Check if pattern matches directory name or filename
            if pattern_lower in pdf.parent.name.lower() or pattern_lower in pdf.name.lower():
                pdf_files.append(pdf)
        
        if not pdf_files:
            console.print(f"[yellow]No PDFs matching '{filter_pattern}' found[/yellow]")
            console.print(f"[dim]Found {len(all_pdf_files)} total PDFs in library[/dim]")
            sys.exit(1)
        
        console.print(f"[cyan]Filtered to {len(pdf_files)} PDFs matching '{filter_pattern}'[/cyan]")
    else:
        pdf_files = all_pdf_files
    
    if not pdf_files:
        console.print("[bold red]No PDFs found in library/[/bold red]")
        console.print("\n[yellow]Add papers first:[/yellow]")
        console.print("  research \"your topic\"")
        sys.exit(1)
    
    console.print(f"\n[dim]Found {len(pdf_files)} PDFs in library[/dim]")
    
    # Index PDFs with progress
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Indexing library (first time is slow)...", total=len(pdf_files))
        
        for pdf_path in pdf_files:
            try:
                logging.debug(f"Adding PDF: {pdf_path}")
                docs.add(pdf_path, settings=settings)
                progress.advance(task)
            except Exception as e:
                logging.error(f"Error adding {pdf_path}: {e}", exc_info=True)
                # Silently skip failed papers - user doesn't need to see each failure
                progress.advance(task)
    
    console.print("[green]✓ Library indexed[/green]\n")
    
    # Query
    with console.status("[bold cyan]Querying library with Gemini 2.5 Flash..."):
        try:
            response = docs.query(question, settings=settings)
            logging.info(f"Query successful: {question}")
            return response
        except Exception as e:
            console.print(f"[bold red]Error querying:[/bold red] {e}")
            logging.error(f"Query error: {e}")
            sys.exit(1)


def export_answer(response, export_dir):
    """Export answer to markdown file."""
    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    
    # Generate filename from question and timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    question_slug = "".join(c if c.isalnum() or c in " -_" else "" for c in response.question[:50])
    question_slug = question_slug.replace(" ", "_").lower()
    filename = f"{timestamp}_{question_slug}.md"
    filepath = export_path / filename
    
    # Create markdown content
    content = f"""# Q&A Session - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Question

{response.question}

## Answer

{response.formatted_answer or response.answer}

## Sources

"""
    
    if hasattr(response, 'contexts') and response.contexts:
        for idx, context in enumerate(response.contexts[:5], 1):
            source_name = context.text.name if hasattr(context.text, 'name') else "Unknown"
            content += f"{idx}. {source_name}"
            if hasattr(context, 'score'):
                content += f" (Relevance: {context.score:.2f})"
            content += "\n"
    
    # Write to file
    filepath.write_text(content)
    console.print(f"\n[green]✓ Exported to:[/green] {filepath}")
    return filepath


def format_answer(response, export_dir=None):
    """Format the answer with citations for display."""
    # Question
    console.print(Panel(
        f"[bold cyan]Question:[/bold cyan] {response.question}",
        border_style="cyan"
    ))
    console.print()
    
    # Answer
    console.print("[bold green]Answer:[/bold green]")
    console.print(response.formatted_answer or response.answer)
    console.print()
    
    # Context/Sources
    if hasattr(response, 'contexts') and response.contexts:
        console.print("[bold yellow]Sources:[/bold yellow]")
        for idx, context in enumerate(response.contexts[:5], 1):
            source_name = context.text.name if hasattr(context.text, 'name') else "Unknown"
            console.print(f"[cyan][{idx}][/cyan] {source_name}")
            if hasattr(context, 'score'):
                console.print(f"    [dim]Relevance: {context.score:.2f}[/dim]")
        console.print()
    
    # Stats
    if hasattr(response, 'context'):
        console.print(f"[dim]Used {len(response.context.split())} words from sources[/dim]")
    
    # Export if requested
    if export_dir:
        export_answer(response, export_dir)


def interactive_chat(library_path, filter_pattern=None, export_dir=None):
    """Interactive chat interface for follow-up questions."""
    from paperqa import Docs
    
    console.print(Panel(
        "[bold cyan]Interactive Q&A Chat[/bold cyan]\n"
        "Ask questions about your library. Type 'exit' or 'quit' to stop.",
        border_style="cyan"
    ))
    console.print()
    
    # Setup
    settings = setup_gemini_settings()
    docs = Docs()
    
    # Index library (same logic as answer_question)
    all_pdf_files = list(library_path.rglob("*.pdf"))
    
    if filter_pattern:
        pdf_files = [p for p in all_pdf_files 
                     if filter_pattern.lower() in p.parent.name.lower() or 
                        filter_pattern.lower() in p.name.lower()]
        console.print(f"[cyan]Using {len(pdf_files)} PDFs matching '{filter_pattern}'[/cyan]")
    else:
        pdf_files = all_pdf_files
        console.print(f"[dim]Found {len(pdf_files)} PDFs[/dim]")
    
    # Index
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("[cyan]Indexing library...", total=len(pdf_files))
        for pdf in pdf_files:
            try:
                docs.add(pdf, settings=settings)
            except:
                pass
            progress.advance(task)
    
    console.print("[green]✓ Ready for questions[/green]\n")
    
    # Chat loop
    while True:
        try:
            question = Prompt.ask("[bold cyan]?[/bold cyan]").strip()
            
            if question.lower() in ['exit', 'quit', 'q']:
                console.print("[dim]Goodbye![/dim]")
                break
            
            if not question:
                continue
            
            # Query
            with console.status("[cyan]Thinking..."):
                response = docs.query(question, settings=settings)
            
            # Display
            console.print()
            console.print("[bold green]Answer:[/bold green]")
            console.print(response.formatted_answer or response.answer)
            console.print()
            
            # Export if requested
            if export_dir:
                export_answer(response, export_dir)
            
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted. Goodbye![/dim]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Ask questions about your research library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  research qa "What is attention mechanism?"
  research qa --papers vaswani "How does attention work?"
  research qa --export qa_sessions "What are transformers?"
  research qa --chat
  research qa --chat --papers attention --export sessions/
        """
    )
    parser.add_argument('question', nargs='*', help='Question to ask')
    parser.add_argument('--papers', '-p', help='Filter to papers matching this pattern (author, title, etc.)')
    parser.add_argument('--export', '-e', help='Export answers to this directory')
    parser.add_argument('--chat', '-c', action='store_true', help='Start interactive chat mode')
    
    args = parser.parse_args()
    
    # Find library directory
    repo_root = Path(__file__).resolve().parent.parent
    library_path = repo_root / "library"
    
    if not library_path.exists():
        console.print(f"[bold red]Library directory not found:[/bold red] {library_path}")
        sys.exit(1)
    
    # Chat mode
    if args.chat:
        console.print(f"\n[bold]Starting Interactive Chat with Gemini 2.5 Flash...[/bold]")
        interactive_chat(library_path, args.papers, args.export)
    else:
        # Single question mode
        if not args.question:
            parser.print_help()
            sys.exit(1)
        
        question = " ".join(args.question)
        console.print(f"\n[bold]Querying library with Gemini 2.5 Flash...[/bold]")
        
        # Get answer
        response = answer_question(question, library_path, args.papers)
        
        # Display result
        format_answer(response, args.export)
