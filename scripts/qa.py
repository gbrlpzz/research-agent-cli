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
    settings.answer.answer_max_sources = 10  # Increased for rigor
    settings.answer.evidence_k = 15  # Increased for rigor (supports 3-5 citations/para)
    
    logging.info(f"Configured paper-qa with Gemini: {settings.llm}")
    return settings


def get_vectordb_path(library_path):
    """Get path to the Qdrant vector database."""
    db_path = library_path / ".qa_vectordb"
    db_path.mkdir(exist_ok=True)
    return db_path


def get_fingerprint_path(library_path):
    """Get path to fingerprint file for cache invalidation."""
    return library_path / ".qa_vectordb" / ".fingerprint"


def get_pdf_fingerprint(pdf_files):
    """Create a fingerprint of PDF files for cache invalidation."""
    import hashlib
    
    # Sort by path and combine path + mtime for each file
    fingerprint_data = []
    for pdf in sorted(pdf_files, key=lambda p: str(p)):
        try:
            mtime = pdf.stat().st_mtime
            fingerprint_data.append(f"{pdf}:{mtime}")
        except:
            fingerprint_data.append(str(pdf))
    
    return hashlib.md5("\n".join(fingerprint_data).encode()).hexdigest()


def check_fingerprint_valid(library_path, expected_fingerprint):
    """Check if stored fingerprint matches expected."""
    fp_path = get_fingerprint_path(library_path)
    if not fp_path.exists():
        return False
    try:
        stored = fp_path.read_text().strip()
        return stored == expected_fingerprint
    except:
        return False


def save_fingerprint(library_path, fingerprint):
    """Save fingerprint to disk."""
    fp_path = get_fingerprint_path(library_path)
    fp_path.parent.mkdir(exist_ok=True)
    fp_path.write_text(fingerprint)


def create_qdrant_docs(client, collection_name="research_papers"):
    """Create a Docs object with Qdrant vector store for persistence."""
    from paperqa import Docs
    from paperqa.llms import QdrantVectorStore
    
    # Create vector store with the persistent client
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name
    )
    
    # Create Docs with the persistent vector store
    docs = Docs(texts_index=vector_store)
    
    logging.info(f"Created Qdrant-backed Docs with client {client}")
    return docs


def load_existing_docs(client, collection_name="research_papers"):
    """Load existing Docs from persistent Qdrant store."""
    from paperqa.llms import QdrantVectorStore
    import asyncio
    
    try:
        # Use the built-in load_docs classmethod to restore full Docs state
        async def async_load():
            return await QdrantVectorStore.load_docs(
                client=client,
                collection_name=collection_name
            )
        
        try:
            # Try to run the async load
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't block in running loop - skip loading
                logging.info("Event loop already running, cannot load synchronously")
                return None
            else:
                docs = loop.run_until_complete(async_load())
        except RuntimeError:
            # No event loop - create one
            docs = asyncio.run(async_load())
        
        if docs and docs.docnames:
            logging.info(f"Loaded Docs with {len(docs.docnames)} documents from Qdrant")
            return docs
        else:
            logging.info("No documents found in Qdrant store")
            return None
            
    except Exception as e:
        logging.warning(f"Failed to load existing Qdrant store: {e}")
        return None


def answer_question(question, library_path, filter_pattern=None):
    """Answer a question using papers in the library."""
    import asyncio
    try:
        return asyncio.run(_async_answer_question(question, library_path, filter_pattern))
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(0)

async def _async_answer_question(question, library_path, filter_pattern=None):
    from paperqa import Docs
    from qdrant_client import AsyncQdrantClient
    
    # Setup settings
    settings = setup_gemini_settings()
    
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
            raise ValueError(f"No PDFs matching '{filter_pattern}' found")
        
        console.print(f"[cyan]Filtered to {len(pdf_files)} PDFs matching '{filter_pattern}'[/cyan]")
    else:
        pdf_files = all_pdf_files
    
    if not pdf_files:
        console.print("[bold red]No PDFs found in library/[/bold red]")
        console.print("\n[yellow]Add papers first:[/yellow]")
        console.print("  research \"your topic\"")
        raise ValueError("No PDFs found in library")
    
    console.print(f"\n[dim]Found {len(pdf_files)} PDFs in library[/dim]")
    
    # Check for persistent vector store (only for non-filtered queries)
    fingerprint = get_pdf_fingerprint(pdf_files)
    
    docs = None
    client = None
    
    try:
        if not filter_pattern:
            # Initialize persistent client ONCE
            db_path = get_vectordb_path(library_path)
            client = AsyncQdrantClient(path=str(db_path))
            
            # Try to load existing Qdrant store
            docs = load_existing_docs(client)
        
        if not docs:
            # Need to (re)index - create new Qdrant-backed Docs
            if filter_pattern:
                # For filtered queries, use in-memory (can't easily filter persistent store)
                docs = Docs()
            else:
                # Create persistent Qdrant store using the SAME client
                docs = create_qdrant_docs(client)
        
        # Optimize: Identify which files actally need indexing
        files_to_index = []
        if docs and docs.docnames and not filter_pattern:
            # Create set of normalized docnames (usually they are filenames or citation keys)
            # PaperQA docnames can vary, but we can check if the stem is present
            indexed_stems = {d.lower() for d in docs.docnames}
            
            for pdf in pdf_files:
                # Check if file stem is in indexed docs
                if pdf.stem.lower() not in indexed_stems:
                    files_to_index.append(pdf)
            
            skipped_count = len(pdf_files) - len(files_to_index)
            if skipped_count > 0:
                console.print(f"[dim]Skipping {skipped_count} already indexed papers[/dim]")
        else:
            files_to_index = pdf_files
        
        if not files_to_index and not filter_pattern:
            console.print("[dim]Library fully indexed (no new papers)[/dim]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Checking library for new papers...", total=len(files_to_index))
            
            for pdf_path in files_to_index:
                try:
                    # docs.add is idempotent - checks hash first
                    await docs.aadd(pdf_path, settings=settings)
                    progress.advance(task)
                except Exception as e:
                    logging.error(f"Error adding {pdf_path}: {e}", exc_info=True)
                    # Silently skip failed papers - user doesn't need to see each failure
                    progress.advance(task)
        
        # CRITICAL: Explicitly build texts index to persist to Qdrant!
        # paper-qa uses lazy indexing - vectors are only written during query
        # We must force the index build here to ensure persistence
        if docs.texts and not filter_pattern:
            with console.status("[cyan]Building vector index..."):
                embedding_model = settings.get_embedding_model()
                await docs._build_texts_index(embedding_model)
                logging.info(f"Built texts index with {len(docs.texts_index)} vectors")
        
        # Save fingerprint for next time (only for non-filtered)
        if not filter_pattern:
            save_fingerprint(library_path, fingerprint)
        
        console.print("[green]✓ Library synchronized[/green]\n")
        
        # Query
        with console.status("[bold cyan]Querying library with Gemini 2.5 Flash..."):
            try:
                response = await docs.aquery(question, settings=settings)
                logging.info(f"Query successful: {question}")
                return response
            except Exception as e:
                console.print(f"[bold red]Error querying:[/bold red] {e}")
                logging.error(f"Query error: {e}")
                sys.exit(1)

    finally:
        if client:
            await client.close()


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
    import asyncio
    try:
        asyncio.run(_async_interactive_chat(library_path, filter_pattern, export_dir))
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. Goodbye![/dim]")
        sys.exit(0)

async def _async_interactive_chat(library_path, filter_pattern=None, export_dir=None):
    from paperqa import Docs
    from qdrant_client import AsyncQdrantClient
    
    console.print(Panel(
        "[bold cyan]Interactive Q&A Chat[/bold cyan]\n"
        "Ask questions about your library. Type 'exit' or 'quit' to stop.",
        border_style="cyan"
    ))
    console.print()
    
    # Setup
    settings = setup_gemini_settings()
    
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
    
    # Check for persistent vector store (only for non-filtered queries)
    fingerprint = get_pdf_fingerprint(pdf_files)
    
    docs = None
    client = None
    
    try:
        if not filter_pattern:
            db_path = get_vectordb_path(library_path)
            client = AsyncQdrantClient(path=str(db_path))
            
            # Try to load existing Qdrant store
            docs = load_existing_docs(client)
        
        if not docs:
            # Need to (re)index
            if filter_pattern:
                docs = Docs()
            else:
                docs = create_qdrant_docs(client)
            
        # Optimize: Identify which files actally need indexing
        files_to_index = []
        if docs and docs.docnames and not filter_pattern:
            indexed_stems = {d.lower() for d in docs.docnames}
            for pdf in pdf_files:
                if pdf.stem.lower() not in indexed_stems:
                    files_to_index.append(pdf)
            skipped_count = len(pdf_files) - len(files_to_index)
            if skipped_count > 0:
                console.print(f"[dim]Skipping {skipped_count} already indexed papers[/dim]")
        else:
            files_to_index = pdf_files

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("[cyan]Checking library for new papers...", total=len(files_to_index))
            for pdf in files_to_index:
                try:
                    # docs.add is idempotent
                    await docs.aadd(pdf, settings=settings)
                except:
                    pass
                progress.advance(task)
        
        # CRITICAL: Explicitly build texts index to persist to Qdrant!
        # paper-qa uses lazy indexing - vectors are only written during query
        if docs.texts and not filter_pattern:
            with console.status("[cyan]Building vector index..."):
                embedding_model = settings.get_embedding_model()
                await docs._build_texts_index(embedding_model)
                logging.info(f"Built texts index with {len(docs.texts_index)} vectors")
        
        # Save fingerprint (only for non-filtered)
        if not filter_pattern:
            save_fingerprint(library_path, fingerprint)
        
        console.print("[green]✓ Library synchronized[/green]\n")
        
        # Chat loop
        while True:
            try:
                # We need to drop out of async context for input? No, just block.
                question = await asyncio.get_event_loop().run_in_executor(None, lambda: Prompt.ask("[bold cyan]?[/bold cyan]").strip())
                
                if question.lower() in ['exit', 'quit', 'q']:
                    console.print("[dim]Goodbye![/dim]")
                    break
                
                if not question:
                    continue
                
                # Query
                with console.status("[cyan]Thinking..."):
                    response = await docs.aquery(question, settings=settings)
                
                # Display
                console.print()
                console.print("[bold green]Answer:[/bold green]")
                console.print(response.formatted_answer or response.answer)
                console.print()
                
                # Export if requested
                if export_dir:
                    export_answer(response, export_dir)
                
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                
    finally:
        if client:
            await client.close()


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
