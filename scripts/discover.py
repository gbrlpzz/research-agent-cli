import sys
import os
import subprocess
from pathlib import Path
from semanticscholar import SemanticScholar
from rich.console import Console
from rich.progress import Progress

console = Console()
sch = SemanticScholar()

def search_and_select(query):
    with console.status(f"[bold green]Searching Semantic Scholar for: {query}..."):
        try:
            results = sch.search_paper(query, limit=20)
        except Exception as e:
            console.print(f"[bold red]Error searching:[/bold red] {e}")
            return None

    if not results:
        console.print("[bold red]No results found.[/bold red]")
        return None

    # Format for FZF
    # Display: "Year | Citations | Title | Authors"
    # Hidden Data: Paper ID / URL (we'll need to parse this back) 
    
    fzf_input = []
    mapping = {} # "Display String" -> Paper Object

    for paper in results:
        title = paper.title
        year = paper.year if paper.year else "????"
        citations = paper.citationCount if paper.citationCount is not None else 0
        authors = ", ".join([a.name for a in paper.authors[:2]]) + (" et al." if len(paper.authors) > 2 else "")
        
        display_str = f"{year} | {citations:5} cites | {title[:60]:<60} | {authors}"
        fzf_input.append(display_str)
        mapping[display_str] = paper

    # Invoke FZF
    try:
        fzf = subprocess.Popen(['fzf', '--multi', '--header', 'Select papers to add (TAB to multi-select)'], 
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        stdout, _ = fzf.communicate(input="\n".join(fzf_input))
        selections = stdout.strip().split('\n')
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] fzf not found. Please install fzf.")
        return None

    selected_urls = []
    for line in selections:
        if line in mapping:
            paper = mapping[line]
            # Prioritize Arxiv URL, then DOI, then S2 URL
            url = None
            if paper.externalIds:
                if 'ArXiv' in paper.externalIds:
                    url = f"https://arxiv.org/abs/{paper.externalIds['ArXiv']}"
                elif 'DOI' in paper.externalIds:
                    url = f"https://doi.org/{paper.externalIds['DOI']}"
            
            if not url:
                url = paper.url # Fallback to S2 URL
            
            if url:
                selected_urls.append(url)
    
    return selected_urls

def add_to_library(urls):
    if not urls:
        return

    # Find papis and config
    # Assuming running from venv, so papis is alongside python
    venv_bin = os.path.dirname(sys.executable)
    papis_cmd = os.path.join(venv_bin, "papis")
    
    repo_root = Path(__file__).resolve().parent.parent
    papis_config = repo_root / "papis.config"

    console.print(f"[bold]Selected {len(urls)} papers. Adding to library...[/bold]")

    with Progress(console=console) as progress:
        task = progress.add_task("[green]Processing...", total=len(urls))
        
        for url in urls:
            progress.console.print(f"[dim]Adding {url}...[/dim]")
            try:
                # Run papis add
                result = subprocess.run(
                    [papis_cmd, "--config", str(papis_config), "add", "--from-url", url],
                    check=True,
                    capture_output=True, 
                    text=True
                )
                if result.stdout:
                    progress.console.print(f"[dim]{result.stdout.strip()}[/dim]")
            except subprocess.CalledProcessError as e:
                progress.console.print(f"[bold red]Failed to add {url}:[/bold red] {e.stderr.strip()}")
            except Exception as e:
                progress.console.print(f"[bold red]Error with {url}:[/bold red] {e}")
            
            progress.advance(task)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("Usage: python discover.py <search query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    urls = search_and_select(query)
    
    if urls:
        add_to_library(urls)
