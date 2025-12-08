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
import logging

# Add parent directory to path for utils
sys.path.insert(0, str(Path(__file__).parent))
from utils.pdf_fetcher import fetch_pdf
from utils import scraper_client

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
    # Use centralized discovery utility
    sys.path.insert(0, str(Path(__file__).parent))
    from utils.discovery import search_papers
    
    # 20 results by default from search_papers
    all_papers = search_papers(query)
    
    if not all_papers:
        return None
    
    # Prepare data for FZF
    # Note: search_papers returns list of dicts. We need to adapt it back for FZF display if needed,
    # or just use the dicts directly since search_papers standardizes output.
    
    fzf_input = []
    papers_data = [] # List of dicts for JSON serialization
    papers_obj = []  # List of dicts for returning
    
    for idx, paper in enumerate(all_papers):
        title = paper['title']
        authors = ", ".join(paper['authors'])
        year = paper['year'] or "????"
        abstract = paper['abstract']
        source_tag = paper['source']
        url = paper['url'] or ""
        citations = paper['citations']
        
        # Format display string
        # Display: Index | URL (hidden) | Source Tag | Year | Citations | Title | Authors
        display_str = f"{idx}|{url}|[{source_tag}] {year} | {citations:5} cites | {title[:45]:<45} | {authors[:30]}"
        fzf_input.append(display_str)
        
        # Store for preview
        papers_data.append({
            'title': title,
            'authors': authors,
            'abstract': abstract,
            'year': str(year),
            'url': url
        })
        papers_obj.append(paper) # It is already a dict
        
    if not fzf_input:
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
            '--bind', 'o:execute-silent(open {2})',
            '--bind', 'q:abort',
            '--header', 'TAB: Select | o: Open in browser | q: Quit | ENTER: Add to library'
        ]
        
        fzf = subprocess.Popen(fzf_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        stdout, _ = fzf.communicate(input="\n".join(fzf_input))
        logging.info("FZF finished. Parsing selections.")
        selections = stdout.strip().split('\n')
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] fzf not found. Please install fzf.")
        os.unlink(tmp_path)
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    
    # Process selections
    selected_urls = []
    for line in selections:
        if not line: continue
        try:
            parts = line.split('|')
            idx = int(parts[0].strip())
            
            if 0 <= idx < len(papers_obj):
                paper = papers_obj[idx]
                
                # Extract identifier based on priority
                if paper.get('arxiv_id'):
                    selected_urls.append(('arxiv', paper['arxiv_id']))
                elif paper.get('doi'):
                    selected_urls.append(('doi', paper['doi']))
                elif paper.get('url'):
                    selected_urls.append(('url', paper['url']))
                    
        except ValueError:
            continue
            
    return selected_urls

    # Prepare data for FZF and temporary storage
    fzf_input = []
    papers_data = [] # List of dicts for JSON serialization
    papers_obj = []  # List of original objects for returning
    
    # Process all merged papers
    for idx, paper_data in enumerate(all_papers):
        source_tag = paper_data['source_tag']
        paper = paper_data['paper']
        source = paper_data['source']
        
        if source == 's2':
            title = paper.title
            year = paper.year if paper.year else "????"
            citations = paper.citationCount if paper.citationCount is not None else 0
            authors_list = [a.name for a in paper.authors]
            abstract = paper.abstract
            
            # Get URL
            url = paper.url or ""
            if paper.externalIds:
                if 'DOI' in paper.externalIds:
                    url = f"https://doi.org/{paper.externalIds['DOI']}"
                elif 'ArXiv' in paper.externalIds:
                    url = f"https://arxiv.org/abs/{paper.externalIds['ArXiv']}"
        else:  # paper-scraper
            title = paper.get('title', 'Untitled')
            year = paper.get('year', '????')
            citations = 0  # paper-scraper doesn't provide citation counts
            authors_list = paper.get('authors', [])
            abstract = paper.get('abstract', 'No abstract available')
            url = paper.get('url', '')
        
        authors_short = ", ".join(authors_list[:2]) + (" et al." if len(authors_list) > 2 else "")
        
        # Display: Index | URL (hidden) | Source Tag | Year | Citations | Title | Authors
        if source == 's2':
            display_str = f"{idx}|{url}|[{source_tag}] {year} | {citations:5} cites | {title[:45]:<45} | {authors_short}"
        else:
            display_str = f"{idx}|{url}|[{source_tag}] {year} |       --    | {title[:45]:<45} | {authors_short}"
        
        fzf_input.append(display_str)
        
        # Store data for preview
        papers_data.append({
            'title': title,
            'authors': ", ".join(authors_list) if authors_list else "Unknown",
            'abstract': abstract,
            'year': str(year),
            'url': url
        })
        papers_obj.append(paper_data)  # Store the full paper_data dict

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
        # Quote paths to handle spaces safely
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

    selected_urls = []
    for line in selections:
        if not line: continue
        try:
            # Extract index from the beginning of the line
            idx_str = line.split('|')[0].strip()
            idx = int(idx_str)
            if 0 <= idx < len(papers_obj):
                paper_data = papers_obj[idx]
                paper = paper_data['paper']
                source = paper_data['source']
                
                identifier = None
                id_source = None
                
                if source == 's2':
                    # Semantic Scholar paper
                    if paper.externalIds:
                        if 'ArXiv' in paper.externalIds:
                            identifier = paper.externalIds['ArXiv']
                            id_source = 'arxiv'
                        elif 'DOI' in paper.externalIds:
                            identifier = paper.externalIds['DOI']
                            id_source = 'doi'
                    
                    if identifier and id_source:
                        selected_urls.append((id_source, identifier))
                    elif paper.url:
                        selected_urls.append(('url', paper.url))
                else:
                    # Paper-scraper result
                    if paper.get('arxiv_id'):
                        selected_urls.append(('arxiv', paper['arxiv_id']))
                    elif paper.get('doi'):
                        selected_urls.append(('doi', paper['doi']))
                    elif paper.get('url'):
                        selected_urls.append(('url', paper['url']))
                    elif paper.get('pdf_path'):
                        # Fallback: use PDF path directly
                        selected_urls.append(('pdf', paper['pdf_path']))

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
            
            # Try to fetch PDF first
            pdf_path = None
            if source == 'arxiv':
                pdf_path = fetch_pdf(arxiv_id=identifier)
            elif source == 'doi':
                pdf_path = fetch_pdf(doi=identifier)
            
            # Construct command
            cmd = [papis_cmd, "--config", str(papis_config), "-l", "main", "add", "--batch"]
            
            if source == 'arxiv':
                cmd.extend(["--from", "arxiv", identifier])
            elif source == 'doi':
                cmd.extend(["--from", "doi", identifier])
            elif source == 'pdf':
                # Direct PDF file from paper-scraper
                cmd.extend(["--file-name", identifier])
            else:
                # Fallback for generic URL
                cmd.append(identifier)
            
            # Add PDF if we fetched one (for arxiv/doi)
            if pdf_path and source != 'pdf':
                cmd.extend(["--file-name", str(pdf_path)])

            try:
                # Print the full command for debugging
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
    logging.info(f"Script started with args: {sys.argv}")
    
    # Check for preview mode
    if len(sys.argv) >= 4 and sys.argv[1] == '--preview':
        # Usage: python discover.py --preview <index> <temp_file>
        preview_paper(sys.argv[2], sys.argv[3])
        sys.exit(0)

    if len(sys.argv) < 2:
        console.print("Usage: python discover.py <search query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    
    # Safeguard against accidental flag processing as query
    if query.startswith("-"):
        console.print(f"[bold red]Invalid query:[/bold red] {query}")
        console.print("Usage: python discover.py <search query>")
        sys.exit(1)

    urls = search_and_select(query)
    
    if urls:
        add_to_library(urls)
