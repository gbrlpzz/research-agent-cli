import sys
import os
import subprocess
from pathlib import Path
from semanticscholar import SemanticScholar
from rich.console import Console
import logging
import sys
import os
import subprocess
import itertools
import json
import tempfile
import re
from pathlib import Path
from semanticscholar import SemanticScholar
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

# Setup logging
logging.basicConfig(
    filename='debug_research.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

console = Console()
sch = SemanticScholar()

def preview_paper(index, temp_file_path):
    """
    Reads the temp file and prints the abstract for the given index.
    Used by FZF preview.
    """
    try:
        with open(temp_file_path, 'r') as f:
            data = json.load(f)
        
        idx = int(index)
        if 0 <= idx < len(data):
            paper = data[idx]
            print(f"\nTitle: {paper.get('title', 'Unknown')}")
            print(f"Authors: {paper.get('authors', 'Unknown')}")
            print("-" * 40)
            print(paper.get('abstract') or "No abstract available.")
        else:
            print("Paper index out of range.")
    except Exception as e:
        print(f"Error reading preview: {e}")

def search_and_select(query):
    logging.info(f"Starting search for: {query}")
    with console.status(f"[bold green]Searching Semantic Scholar for: {query}..."):
        try:
            # We ask for a limit, but we must also limit iteration manually to be safe
            results = sch.search_paper(query, limit=20)
        except Exception as e:
            console.print(f"[bold red]Error searching:[/bold red] {e}")
            logging.error(f"Error searching: {e}")
            return None

    if not results:
        console.print("[bold red]No results found.[/bold red]")
        logging.info("No results found.")
        return None

    logging.info(f"Search returned (generator). Processing first 20 results.")

    # Prepare data for FZF and temporary storage
    fzf_input = []
    papers_data = [] # List of dicts for JSON serialization
    papers_obj = []  # List of original objects for returning
    
    # Use islice to ensure we only trigger requests for the first 20
    for idx, paper in enumerate(itertools.islice(results, 20)):
        title = paper.title
        year = paper.year if paper.year else "????"
        citations = paper.citationCount if paper.citationCount is not None else 0
        authors_list = [a.name for a in paper.authors]
        authors_short = ", ".join(authors_list[:2]) + (" et al." if len(authors_list) > 2 else "")
        
        # Display: Index | Year | Citations | Title | Authors
        display_str = f"{idx} | {year} | {citations:5} cites | {title[:50]:<50} | {authors_short}"
        fzf_input.append(display_str)
        
        # Store data for preview
        papers_data.append({
            'title': title,
            'authors': ", ".join(authors_list),
            'abstract': paper.abstract,
            'year': year
        })
        papers_obj.append(paper)

    if not fzf_input:
        console.print("[bold red]No results found (empty list).[/bold red]")
        logging.info("No results extracted from generator.")
        return None

    # Create temp file for preview
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp_file:
        json.dump(papers_data, tmp_file)
        tmp_path = tmp_file.name

    # Invoke FZF
    try:
        logging.info("Invoking FZF subprocess with preview.")
        # Command to run this script in preview mode
        preview_cmd = f"{sys.executable} {os.path.abspath(__file__)} --preview {{1}} {tmp_path}"
        
        fzf_args = [
            'fzf', 
            '--multi', 
            '--delimiter', '|',
            '--with-nth', '2..', # Hide index from display (field 1 is index)
            '--preview', preview_cmd,
            '--preview-window', 'right:50%:wrap',
            '--bind', 'ctrl-a:select-all,ctrl-d:deselect-all,ctrl-t:toggle-all',
            '--header', 'TAB: Select/Unselect | Ctrl-A: Select All | ENTER: Confirm'
        ]
        
        fzf = subprocess.Popen(fzf_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        stdout, _ = fzf.communicate(input="\n".join(fzf_input))
        logging.info("FZF finished. Parsing selections.")
        selections = stdout.strip().split('\n')
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] fzf not found. Please install fzf.")
        logging.error("fzf not found.")
        os.unlink(tmp_path)
        return None
    finally:
        # Cleanup
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    selected_urls = []
    for line in selections:
        if not line: continue
        try:
            # Extract index from the beginning of the line
            idx_str = line.split('|')[0].strip()
            idx = int(idx_str)
            if 0 <= idx < len(papers_obj):
                paper = papers_obj[idx]
                # Pass the paper object directly or its ID if possible? 
                # We need to construct a robust identifier for papis.
                
                identifier = None
                source = None
                
                if paper.externalIds:
                    if 'ArXiv' in paper.externalIds:
                        identifier = paper.externalIds['ArXiv']
                        source = 'arxiv'
                    elif 'DOI' in paper.externalIds:
                        identifier = paper.externalIds['DOI']
                        source = 'doi'
                
                # If we have an ID and Source, that's best.
                # If not, maybe use URL?
                if identifier and source:
                    selected_urls.append((source, identifier))
                elif paper.url:
                    # Fallback to URL (less reliable with current papis flags)
                    # But wait, papis add <URL> usually works if it auto-detects.
                    # Let's try to pass the raw URL and let papis figure it out if we lack IDs.
                    # But the previous error said 'https...' is not a valid FROM.
                    # That means we shouldn't use --from if we use a URL.
                    # We'll handle this in add_to_library.
                    selected_urls.append(('url', paper.url))

        except ValueError:
            logging.error(f"Could not parse index from line: {line}")
            continue
    
    console.print(f"[dim]Debug: FZF finished. Selected {len(selected_urls)} papers.[/dim]")
    logging.info(f"Selected {len(selected_urls)} papers: {selected_urls}")
    return selected_urls

def add_to_library(items):
    """
    items: List of (source, identifier) tuples.
    e.g. ('arxiv', '1706.03762'), ('doi', '10.1234/5678'), ('url', 'https://...')
    """
    if not items:
        logging.info("No items to add.")
        return

    # Find papis and config
    venv_bin = os.path.dirname(sys.executable)
    papis_cmd = os.path.join(venv_bin, "papis")
    
    repo_root = Path(__file__).resolve().parent.parent
    papis_config = repo_root / "papis.config"

    console.print(f"[bold]Selected {len(items)} papers. Adding to library...[/bold]")
    console.print("[dim]Debug: Entering add_to_library logic...[/dim]")
    logging.info("Entering add_to_library loop.")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[green]Starting...", total=len(items))
        
        for source, identifier in items:
            progress.update(task, description=f"[green]Adding {source}:{identifier}...[/green]")
            logging.info(f"Adding: source={source}, id={identifier}")
            
            # Construct command
            # If source is 'url', we generally just pass the URL as a positional arg, 
            # OR we try to find a --from-url if available (removed in newer versions?)
            # or rely on auto-detection.
            # If source is 'arxiv', use --from arxiv <id>
            # If source is 'doi', use --from doi <id>
            
            cmd = [papis_cmd, "--config", str(papis_config), "add", "--lib", "main"]
            
            if source == 'arxiv':
                cmd.extend(["--from", "arxiv", identifier])
            elif source == 'doi':
                cmd.extend(["--from", "doi", identifier])
            else:
                # Fallback for generic URL. 
                # Papis 'add' takes [FILES]... which can be URLs.
                # It will try to detect.
                cmd.append(identifier)

            try:
                logging.debug(f"Executing: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True, 
                    text=True,
                    timeout=120 # Increased timeout for downloads
                )
                logging.info(f"Finished adding {identifier}. Return code: {result.returncode}")
                if result.stdout:
                    logging.debug(f"Stdout: {result.stdout.strip()}")
                    progress.console.print(f"[dim]{result.stdout.strip()}[/dim]")
            except subprocess.TimeoutExpired:
                logging.error(f"Timeout expired for {identifier}")
                progress.console.print(f"[bold red]Timeout adding {identifier}[/bold red]")
            except subprocess.CalledProcessError as e:
                logging.error(f"CalledProcessError for {identifier}: {e.stderr}")
                progress.console.print(f"[bold red]Failed to add {identifier}:[/bold red] {e.stderr.strip()}")
            except Exception as e:
                logging.error(f"Exception for {identifier}: {e}")
                progress.console.print(f"[bold red]Error with {identifier}:[/bold red] {e}")
            
            progress.advance(task)

if __name__ == "__main__":
    logging.info("Script started.")
    
    # Check for preview mode
    if len(sys.argv) >= 4 and sys.argv[1] == '--preview':
        # Usage: python discover.py --preview <index> <temp_file>
        preview_paper(sys.argv[2], sys.argv[3])
        sys.exit(0)

    if len(sys.argv) < 2:
        console.print("Usage: python discover.py <search query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    urls = search_and_select(query)
    
    if urls:
        add_to_library(urls)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("Usage: python discover.py <search query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    urls = search_and_select(query)
    
    if urls:
        add_to_library(urls)
