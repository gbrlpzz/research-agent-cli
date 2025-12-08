#!/usr/bin/env python3
"""
Question-answering system using paper-qa with Gemini.
Queries all PDFs in the local library to answer questions with citations.
"""
import sys
import os
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from dotenv import load_dotenv
import logging

# Setup
console = Console()
load_dotenv()

# Logging
logging.basicConfig(
    filename='debug_research.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

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
    
    # Configure settings for Gemini
    settings = Settings()
    settings.llm = "gemini/gemini-2.0-flash-exp"  # Fast and free
    settings.summary_llm = "gemini/gemini-2.0-flash-exp"
    settings.embedding = "text-embedding-3-small"  # Uses OpenAI embeddings (cheap)
    settings.answer.answer_max_sources = 5
    settings.answer.evidence_k = 10
    
    logging.info(f"Configured paper-qa with Gemini: {settings.llm}")
    return settings


def answer_question(question, library_path):
    """Answer a question using papers in the library."""
    from paperqa import Docs
    
    # Setup settings
    settings = setup_gemini_settings()
    
    # Create Docs object
    docs = Docs()
    
    # Find all PDFs in library
    pdf_files = list(library_path.rglob("*.pdf"))
    
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
                logging.error(f"Error adding {pdf_path}: {e}")
                progress.console.print(f"[yellow]Skip: {pdf_path.name}[/yellow]")
                progress.advance(task)
    
    console.print("[green]✓ Library indexed[/green]\n")
    
    # Query
    with console.status("[bold cyan]Querying library with Gemini..."):
        try:
            response = docs.query(question, settings=settings)
            logging.info(f"Query successful: {question}")
            return response
        except Exception as e:
            console.print(f"[bold red]Error querying:[/bold red] {e}")
            logging.error(f"Query error: {e}")
            sys.exit(1)


def format_answer(response):
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("Usage: python qa.py <question>")
        console.print("\nExamples:")
        console.print('  research qa "What is attention mechanism?"')
        console.print('  research qa "How do transformers work?"')
        sys.exit(1)
    
    # Get question from args
    question = " ".join(sys.argv[1:])
    
    # Find library directory
    repo_root = Path(__file__).resolve().parent.parent
    library_path = repo_root / "library"
    
    if not library_path.exists():
        console.print(f"[bold red]Library directory not found:[/bold red] {library_path}")
        sys.exit(1)
    
    console.print(f"\n[bold]Querying library with Gemini 2.0 Flash...[/bold]")
    
    # Get answer
    response = answer_question(question, library_path)
    
    # Display result
    format_answer(response)
