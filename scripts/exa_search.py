import sys
import os
import subprocess
import itertools
import json
import tempfile
import re
from pathlib import Path
from exa_py import Exa
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
import logging

# Add parent directory to path for utils
sys.path.insert(0, str(Path(__file__).parent))
from utils.pdf_fetcher import fetch_pdf

# Setup logging
logging.basicConfig(
    filename='debug_research.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

console = Console()

# Load environment variables
load_dotenv()
api_key = os.getenv('EXA_API_KEY')

if not api_key:
    console.print("[bold red]Error:[/bold red] EXA_API_KEY not found in .env file")
    console.print("Please create a .env file with your Exa.ai API key:")
    console.print("  EXA_API_KEY=your_key_here")
    sys.exit(1)

exa = Exa(api_key=api_key)

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
            print(f"URL: {paper.get('url', 'Unknown')}")
            print("-" * 40)
            print(paper.get('abstract') or "No abstract available.")
        else:
            print("Paper index out of range.")
    except Exception as e:
        print(f"Error reading preview: {e}")

def extract_doi_from_url(url):
    """Extract DOI from various URL formats."""
    if not url:
        return None
    
    # Match doi.org URLs
    doi_match = re.search(r'doi\.org/(10\.\d+/[^\s]+)', url)
    if doi_match:
        return doi_match.group(1)
    
    # Match DOIs embedded in other URLs
    doi_match = re.search(r'(10\.\d+/[^\s]+)', url)
    if doi_match:
        return doi_match.group(1)
    
    return None

def extract_arxiv_from_url(url):
    """Extract arXiv ID from URL."""
    if not url:
        return None
    
    # Match arxiv.org URLs
    arxiv_match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', url)
    if arxiv_match:
        return arxiv_match.group(1)
    
    return None

def search_and_select(query):
    logging.info(f"Starting Exa.ai search for: {query}")
    console.print(f"[dim]Using Exa.ai semantic search (costs credits)[/dim]")
    
    with console.status(f"[bold green]Searching Exa.ai for: {query}..."):
        try:
            # Search with filters for academic content
            results = exa.search_and_contents(
                query,
                num_results=20,
                category="research paper",
                include_domains=[
                    "arxiv.org",
                    "doi.org",
                    "nature.com",
                    "science.org",
                    "springer.com",
                    "sciencedirect.com",
                    "ieee.org",
                    "acm.org",
                    "pubmed.ncbi.nlm.nih.gov",
                    "biorxiv.org",
                    "medrxiv.org"
                ],
                text={"max_characters": 2000}  # Get text for abstract extraction
            )
        except Exception as e:
            console.print(f"[bold red]Error searching:[/bold red] {e}")
            logging.error(f"Error searching Exa.ai: {e}")
            return None

    if not results or not results.results:
        console.print("[bold red]No results found.[/bold red]")
        logging.info("No results found from Exa.ai.")
        return None

    logging.info(f"Exa.ai returned {len(results.results)} results")

    # Prepare data for FZF and temporary storage
    fzf_input = []
    papers_data = []
    papers_metadata = []
    
    for idx, result in enumerate(results.results):
        title = result.title or "Unknown Title"
        url = result.url or ""
        
        # Extract year from published_date if available
        year = "????"
        if hasattr(result, 'published_date') and result.published_date:
            year = result.published_date[:4]
        
        # Try to extract author from text or use domain as fallback
        authors = "Unknown"
        if hasattr(result, 'author') and result.author:
            authors = result.author
        
        # Extract abstract from text content
        abstract = "No abstract available."
        if hasattr(result, 'text') and result.text:
            # Take first 500 chars as abstract approximation
            abstract = result.text[:500] + "..." if len(result.text) > 500 else result.text
        
        # Score reflects relevance (0-1)
        score = getattr(result, 'score', 0)
        score_pct = int(score * 100) if score else 0
        
        # Display: Index | URL (hidden) | Year | Score | Title | Authors
        display_str = f"{idx}|{url}|{year} | {score_pct:3}% rel | {title[:50]:<50} | {authors[:30]}"
        fzf_input.append(display_str)
        
        # Store data for preview
        papers_data.append({
            'title': title,
            'authors': authors,
            'abstract': abstract,
            'year': year,
            'url': url
        })
        
        # Store metadata for adding to library
        papers_metadata.append({
            'url': url,
            'title': title,
            'authors': authors,
            'year': year
        })

    if not fzf_input:
        console.print("[bold red]No results found (empty list).[/bold red]")
        logging.info("No results extracted from Exa.ai response.")
        return None

    # Create temp file for preview
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp_file:
        json.dump(papers_data, tmp_file)
        tmp_path = tmp_file.name

    # Invoke FZF
    try:
        logging.info("Invoking FZF subprocess with preview.")
        preview_cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --preview {{1}} "{tmp_path}"'
        
        fzf_args = [
            'fzf', 
            '--multi', 
            '--delimiter', '|',
            '--with-nth', '3..', # Hide index and URL from display
            '--preview', preview_cmd,
            '--preview-window', 'right:50%:wrap',
            '--bind', 'ctrl-a:select-all,ctrl-d:deselect-all,ctrl-t:toggle-all',
            '--bind', 'o:execute-silent(open {2})',  # Press 'o' to open URL in browser
            '--bind', 'q:abort',  # Press 'q' to quit
            '--header', 'TAB: Select | o: Open in browser | q: Quit | ENTER: Add to library'
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

    selected_items = []
    for line in selections:
        if not line: continue
        try:
            # Extract index from the beginning of the line
            idx_str = line.split('|')[0].strip()
            idx = int(idx_str)
            if 0 <= idx < len(papers_metadata):
                metadata = papers_metadata[idx]
                url = metadata['url']
                
                # Try to extract DOI or ArXiv ID
                doi = extract_doi_from_url(url)
                arxiv = extract_arxiv_from_url(url)
                
                identifier = None
                source = None
                
                if arxiv:
                    identifier = arxiv
                    source = 'arxiv'
                elif doi:
                    identifier = doi
                    source = 'doi'
                elif url:
                    # Fallback to URL
                    identifier = url
                    source = 'url'
                
                if identifier and source:
                    selected_items.append((source, identifier))

        except ValueError:
            logging.error(f"Could not parse index from line: {line}")
            continue
    
    console.print(f"[dim]Debug: FZF finished. Selected {len(selected_items)} papers.[/dim]")
    logging.info(f"Selected {len(selected_items)} papers: {selected_items}")
    return selected_items

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
            
            # Try to fetch PDF first
            pdf_path = None
            if source == 'arxiv':
                pdf_path = fetch_pdf(arxiv_id=identifier)
            elif source == 'doi':
                pdf_path = fetch_pdf(doi=identifier)
            
            cmd = [papis_cmd, "--config", str(papis_config), "-l", "main", "add", "--batch"]
            
            if source == 'arxiv':
                cmd.extend(["--from", "arxiv", identifier])
            elif source == 'doi':
                cmd.extend(["--from", "doi", identifier])
            else:
                # Fallback for generic URL
                cmd.append(identifier)
            
            # Add PDF if we got one
            if pdf_path:
                cmd.extend(["--file", str(pdf_path)])

            try:
                progress.console.print(f"[dim]Executing papis: {' '.join(cmd)}[/dim]")
                logging.debug(f"Executing: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True, 
                    text=True,
                    timeout=120
                )
                logging.info(f"Finished adding {identifier}. Return code: {result.returncode}")
                if result.stdout:
                    logging.debug(f"Stdout: {result.stdout.strip()}")
                    progress.console.print(f"[dim]{result.stdout.strip()}[/dim]")
                
                # Safe export to master.bib
                try:
                    sys.path.insert(0, str(repo_root / "scripts"))
                    from utils.sync_bib import sync_master_bib
                    if sync_master_bib():
                         logging.info(f"Updated master.bib")
                    else:
                         logging.error("Failed to update master.bib")
                         progress.console.print("[yellow]Warning: Failed to update master.bib[/yellow]")
                except Exception as ex:
                    logging.error(f"Error calling sync_master_bib: {ex}")
                
            except subprocess.TimeoutExpired:
                logging.error(f"Timeout expired for {identifier}")
                progress.console.print(f"[bold red]Timeout adding {identifier}[/bold red]")
            except subprocess.CalledProcessError as e:
                logging.error(f"CalledProcessError for {identifier}: {e.stderr}")
                progress.console.print(f"[bold red]Failed to add {identifier}:[/bold red] {e.stderr.strip()}")
            except Exception as e:
                logging.error(f"Exception for {identifier}: {e}")
                progress.console.print(f"[bold red]Error with {identifier}:[/bold red] {e}")
            
            # Cleanup temp PDF if exists
            if pdf_path and pdf_path.exists():
                try:
                    pdf_path.unlink()
                except:
                    pass
            
            progress.advance(task)

if __name__ == "__main__":
    logging.info(f"Exa search script started with args: {sys.argv}")
    
    # Check for preview mode
    if len(sys.argv) >= 4 and sys.argv[1] == '--preview':
        preview_paper(sys.argv[2], sys.argv[3])
        sys.exit(0)

    if len(sys.argv) < 2:
        console.print("Usage: python exa_search.py <search query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    
    # Safeguard against accidental flag processing as query
    if query.startswith("-"):
        console.print(f"[bold red]Invalid query:[/bold red] {query}")
        console.print("Usage: python exa_search.py <search query>")
        sys.exit(1)

    items = search_and_select(query)
    
    if items:
        add_to_library(items)
