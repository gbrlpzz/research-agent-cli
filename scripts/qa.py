#!/usr/bin/env python3
"""
Question-answering system using PaperQA (configurable models via LiteLLM).
Queries all PDFs in the local library to answer questions with citations.
Supports subset filtering, answer export, and interactive chat.
"""
import sys
import os
import argparse
import asyncio
import warnings
import pickle
import json
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

# Model routing (reasoning vs RAG)
from utils.model_config import ModelRouting, ensure_model_env

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

def setup_paperqa_settings(
    *,
    rag_model: "Optional[str]" = None,
    embedding_model: "Optional[str]" = None,
):
    """Configure paper-qa Settings for library RAG (model is configurable)."""
    from paperqa import Settings
    
    routing = ModelRouting.from_env(rag_model=rag_model, embedding_model=embedding_model)

    try:
        ensure_model_env(routing.rag_model)
        ensure_model_env(routing.embedding_model)
    except RuntimeError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        console.print("\n[yellow]Configuration:[/yellow]")
        console.print("  - Default RAG model: openai/gpt-5.2-fast")
        console.print("  - Default embedding: openai/text-embedding-3-large")
        console.print("\n[yellow]Set API keys in .env:[/yellow]")
        console.print("  OPENAI_API_KEY=...")
        console.print("  # or for Gemini:")
        console.print("  GEMINI_API_KEY=...")
        sys.exit(1)
    
    # Suppress LiteLLM verbose output (PaperQA uses LiteLLM internally)
    try:
        import litellm
        litellm.suppress_debug_info = True
        litellm.set_verbose = False
    except Exception:
        pass
    
    # Configure settings
    settings = Settings()
    settings.llm = routing.rag_model
    settings.summary_llm = routing.rag_model
    settings.embedding = routing.embedding_model
    settings.answer.answer_max_sources = 10  # Increased for rigor
    settings.answer.evidence_k = 15  # Increased for rigor (supports 3-5 citations/para)
    
    # Register LiteLLM callback for token tracking
    def track_usage(kwargs, completion_response, start_time, end_time):
        try:
            from phases.orchestrator import get_orchestrator
            orch = get_orchestrator()
            if orch and orch._current_phase and hasattr(completion_response, 'usage'):
                usage = completion_response.usage
                input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
                output_tokens = getattr(usage, 'completion_tokens', 0) or 0
                model = kwargs.get('model') or completion_response.model
                
                orch.record_tokens(
                    orch._current_phase, 
                    input_tokens, 
                    output_tokens,
                    model=model
                )
        except Exception:
            pass

    try:
        import litellm
        # Add to success callbacks if not already present to avoid duplicates
        # We use a unique name check or just append safely
        if track_usage not in litellm.success_callback:
            litellm.success_callback.append(track_usage)
    except Exception:
        pass
    
    logging.info(f"Configured paper-qa: llm={settings.llm}, embedding={settings.embedding}")
    return settings


def get_vectordb_path(library_path):
    """Get path to the Qdrant vector database."""
    db_path = library_path / ".qa_vectordb"
    db_path.mkdir(exist_ok=True)
    return db_path





def compute_md5(file_path):
    """Compute MD5 hash of a file."""
    import hashlib
    try:
        return hashlib.md5(file_path.read_bytes()).hexdigest()
    except Exception:
        return None

def get_blacklist_path(library_path):
    return library_path / ".qa_blacklist"

def load_blacklist(library_path):
    path = get_blacklist_path(library_path)
    if path.exists():
        try:
            return set(path.read_text().splitlines())
        except:
            pass
    return set()

def get_manifest_path(library_path):
    return library_path / ".qa_manifest.json"

def load_manifest(library_path):
    path = get_manifest_path(library_path)
    if path.exists():
        try:
            d = json.loads(path.read_text())
            return d
        except Exception as e:
            logging.error(f"Error loading manifest: {e}")
            pass
    return {}

def save_manifest(library_path, manifest):
    path = get_manifest_path(library_path)
    try:
        path.write_text(json.dumps(manifest, indent=2))
    except:
        pass

def add_to_blacklist(library_path, filename):
    blacklist = load_blacklist(library_path)
    blacklist.add(filename)
    try:
        get_blacklist_path(library_path).write_text("\n".join(blacklist))
    except:
        pass

def get_pickle_path(library_path):
    """Get path to the pickled Docs object."""
    return library_path / ".qa_docs.pkl"


def load_existing_docs(library_path):
    """Load existing Docs from pickle cache."""
    pkl_path = get_pickle_path(library_path)
    if not pkl_path.exists():
        return None
        
    try:
        with open(pkl_path, 'rb') as f:
            docs = pickle.load(f)
        if docs and docs.docnames:
            logging.info(f"Loaded Docs with {len(docs.docnames)} documents from pickle")
            return docs
    except Exception as e:
        logging.warning(f"Failed to load existing pickle: {e}")
        return None
    return None


def save_docs(library_path, docs):
    """Save Docs object to pickle cache."""
    pkl_path = get_pickle_path(library_path)
    try:
        with open(pkl_path, 'wb') as f:
            pickle.dump(docs, f)
        logging.info("Saved Docs to pickle")
    except Exception as e:
        logging.error(f"Failed to save pickle: {e}")

def get_fingerprint_path(library_path):
    """Get path to the fingerprint file."""
    return library_path / ".qa_fingerprint"

def load_fingerprint(library_path):
    """Load existing library fingerprint."""
    fp_path = get_fingerprint_path(library_path)
    if fp_path.exists():
        try:
            with open(fp_path, 'r') as f:
                return f.read().strip()
        except:
            pass
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
    from paperqa import Docs
    
    # Setup settings
    settings = setup_paperqa_settings()
    
    # Find all PDFs in library
    all_pdf_files = list(library_path.rglob("*.pdf"))
    
    # Track if we should use manifest (default: yes if no filter or if filter fallback)
    use_manifest = True
    
    # Filter PDFs if pattern provided
    if filter_pattern:
        pdf_files = []
        pattern_lower = filter_pattern.lower()
        for pdf in all_pdf_files:
            # Check if pattern matches directory name or filename
            if pattern_lower in pdf.parent.name.lower() or pattern_lower in pdf.name.lower():
                pdf_files.append(pdf)
        
        if not pdf_files:
            # Graceful fallback: use all PDFs instead of erroring
            # IMPORTANT: Still use manifest to avoid re-indexing!
            console.print(f"[dim]Filter '{filter_pattern}' matched no PDFs, querying full library[/dim]")
            pdf_files = all_pdf_files
            use_manifest = True  # Fallback to full library - use manifest!
        else:
            console.print(f"[cyan]Filtered to {len(pdf_files)} PDFs matching '{filter_pattern}'[/cyan]")
            use_manifest = False  # Genuine filter - don't use manifest (user wants specific subset)
    else:
        pdf_files = all_pdf_files
    
    if not pdf_files:
        console.print("[bold red]No PDFs found in library/[/bold red]")
        console.print("\n[yellow]Add papers first:[/yellow]")
        console.print("  research \"your topic\"")
        raise ValueError("No PDFs found in library")
    
    console.print(f"\n[dim]Found {len(pdf_files)} PDFs in library[/dim]")
    
    docs = None
    try:
        if use_manifest:
            # Try to load existing docs from pickle
            docs = load_existing_docs(library_path)
        
        if not docs:
            # Need to (re)index - create new Docs
            docs = Docs()
            
        if docs and hasattr(docs, 'docs'):
            logging.info(f"Loaded {len(docs.docs) if docs and hasattr(docs, 'docs') else 0} docs from cache")

        # Identify new files to index by Content Hash via Manifest
        files_to_index = []
        files_hashes = {} # Map path -> hash
        
        if use_manifest:
            # Load Blacklist & Manifest
            blacklist = load_blacklist(library_path)
            manifest = load_manifest(library_path)

            for pdf in pdf_files:
                # Check blacklist first
                if pdf.name in blacklist:
                    continue
                
                # Check Manifest
                file_hash = compute_md5(pdf)
                if file_hash:
                    # If file is in manifest AND hash matches, it's already indexed consistently
                    if pdf.name in manifest and manifest[pdf.name] == file_hash:
                        continue
                    
                    # Otherwise index it
                    files_to_index.append(pdf)
                    files_hashes[pdf] = file_hash
        else:
            files_to_index = pdf_files

        if not files_to_index and not filter_pattern:
            console.print("[dim]Library fully indexed (no new content)[/dim]")
        else:
            logging.info(f"Need to index {len(files_to_index)} papers")
        
        if files_to_index:
            console.print(f"[cyan]Indexing {len(files_to_index)} new papers...[/cyan]")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Adding papers to index...", total=len(files_to_index))
                
                for i, pdf_path in enumerate(files_to_index, 1):
                    try:
                        logging.info(f"Adding paper {i}/{len(files_to_index)}: {pdf_path.name}")
                        progress.update(task, description=f"[cyan]Indexing {pdf_path.name}...")
                        
                        # Add with timeout, enforcing dockey=MD5
                        try:
                            file_hash = files_hashes.get(pdf_path)
                            async with asyncio.timeout(60):  # 60s per paper
                                # Pass dockey to ensure persistence stability
                                await docs.aadd(pdf_path, dockey=file_hash, settings=settings)
                            
                            # Success: Update Manifest
                            if file_hash:
                                manifest = load_manifest(library_path)
                                manifest[pdf_path.name] = file_hash
                                save_manifest(library_path, manifest)
                                
                            logging.info(f"Successfully added {pdf_path.name}")
                        except asyncio.TimeoutError:
                            logging.error(f"Timeout adding {pdf_path.name}")
                            console.print(f"[yellow]⚠ Timeout: {pdf_path.name}[/yellow]")
                        
                        progress.advance(task)
                    except Exception as e:
                        logging.error(f"Error adding {pdf_path}: {e}", exc_info=True)
                        console.print(f"[yellow]⚠ Failed: {pdf_path.name} - {e}[/yellow]")
                        # Add to blacklist using logic
                        if "not look like a text document" in str(e) or "Empty file" in str(e):
                             add_to_blacklist(library_path, pdf_path.name)
                             console.print(f"[red]  -> Added {pdf_path.name} to blacklist (will skip next time)[/red]")
                        progress.advance(task)
        
        # CRITICAL: Explicitly build texts index to persist to Qdrant!
        # paper-qa uses lazy indexing - vectors are only written during query
        # We must force the index build here to ensure persistence
        # REVERTED: Explicit build causes Qdrant client closure crash.
        # PaperQA2 will build the index automatically during the first query/get_evidence call.
        # This lazy indexing is robust and will persist normally.
        if docs.texts and not filter_pattern:
            logging.info(f"Have {len(docs.texts)} texts ready for query-time indexing")
        # Save docs to pickle for persistence
        if not filter_pattern and len(files_to_index) > 0:
            save_docs(library_path, docs)
            console.print("[green]✓ Saved database to disk[/green]")
        console.print(f"[cyan]Docs ready ({len(docs.texts)} chunks). Indexing happens during query.[/cyan]")
        
        
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

    except Exception:
        raise


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
    from paperqa import Docs
    
    console.print(Panel(
        "[bold cyan]Interactive Q&A Chat[/bold cyan]\n"
        "Ask questions about your library. Type 'exit' or 'quit' to stop.",
        border_style="cyan"
    ))
    console.print()
    
    # Setup
    settings = setup_paperqa_settings()
    
    # Index library (same logic as answer_question)
    all_pdf_files = list(library_path.rglob("*.pdf"))
    
    # Track if we should use manifest
    use_manifest = True
    
    if filter_pattern:
        pdf_files = [p for p in all_pdf_files 
                     if filter_pattern.lower() in p.parent.name.lower() or 
                        filter_pattern.lower() in p.name.lower()]
        if pdf_files:
            console.print(f"[cyan]Using {len(pdf_files)} PDFs matching '{filter_pattern}'[/cyan]")
            use_manifest = False  # Genuine filter
        else:
            console.print(f"[dim]Filter '{filter_pattern}' matched no PDFs, using full library[/dim]")
            pdf_files = all_pdf_files
            use_manifest = True  # Fallback - use manifest
    else:
        pdf_files = all_pdf_files
        console.print(f"[dim]Found {len(pdf_files)} PDFs[/dim]")
    
    docs = None
    try:
        if use_manifest:
            # Try to load existing docs from pickle
            docs = load_existing_docs(library_path)
        
        if not docs:
            # Need to (re)index - create new Docs
            docs = Docs()
            
        # Collect existing hashes (dockeys)
        indexed_hashes = set()
        if docs and hasattr(docs, 'docs'):
            for d in docs.docs.values():
                if hasattr(d, 'dockey'):
                    indexed_hashes.add(d.dockey)
        
        # Identify new files to index by Content Hash
        files_to_index = []
        files_hashes = {} # Map path -> hash
        
        if use_manifest:
            for pdf in pdf_files:
                file_hash = compute_md5(pdf)
                if file_hash:
                    if file_hash not in indexed_hashes:
                        files_to_index.append(pdf)
                        files_hashes[pdf] = file_hash
        else:
            files_to_index = pdf_files

        if not files_to_index and use_manifest:
            console.print("[dim]Library fully indexed (no new content)[/dim]")
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("[cyan]Checking library for new papers...", total=len(files_to_index))
            for pdf in files_to_index:
                try:
                    # docs.add is idempotent
                    file_hash = files_hashes.get(pdf)
                    await docs.aadd(pdf, dockey=file_hash, settings=settings)
                except:
                    pass
                progress.advance(task)
        
        # Save docs to pickle for persistence
        if use_manifest and len(files_to_index) > 0:
            save_docs(library_path, docs)
            console.print("[green]✓ Saved database to disk[/green]")
        
        
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
                
    except Exception:
        raise


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
        routing = ModelRouting.from_env()
        console.print(f"\n[bold]Starting Interactive Chat[/bold]")
        console.print(f"[dim]RAG model: {routing.rag_model}[/dim]")
        interactive_chat(library_path, args.papers, args.export)
    else:
        # Single question mode
        if not args.question:
            parser.print_help()
            sys.exit(1)
        
        question = " ".join(args.question)
        routing = ModelRouting.from_env()
        console.print(f"\n[bold]Querying library[/bold]")
        console.print(f"[dim]RAG model: {routing.rag_model}[/dim]")
        
        # Get answer
        response = answer_question(question, library_path, args.papers)
        
        # Display result
        format_answer(response, args.export)
