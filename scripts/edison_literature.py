"""
Edison Scientific Literature Agent Integration
Provides AI-powered literature synthesis with automatic citation extraction.
"""
import sys
import os
import json
import re
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import logging

# Conditional imports with error handling
try:
    from edison_client import EdisonClient, JobNames
    EDISON_AVAILABLE = True
except ImportError:
    EDISON_AVAILABLE = False
    print("Warning: edison-client not installed. Run: pip install edison-client")

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.progress import Progress, SpinnerColumn, TextColumn

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))
from utils.pdf_fetcher import fetch_pdf

# Setup
console = Console()
logging.basicConfig(
    filename='debug_research.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()
api_key = os.getenv('EDISON_API_KEY')

if not api_key or api_key == 'your_edison_key_here':
    console.print("[bold red]Error:[/bold red] EDISON_API_KEY not found or not set in .env file")
    console.print("Please add your Edison API key to .env:")
    console.print("  EDISON_API_KEY=your_actual_key_here")
    console.print("\\nGet your API key from: https://platform.edisonscientific.com/profile")
    sys.exit(1)

if not EDISON_AVAILABLE:
    console.print("[bold red]Error:[/bold red] edison-client not installed")
    console.print("Install it with: pip install edison-client")
    sys.exit(1)

# Initialize Edison client
edison = EdisonClient(api_key=api_key)

# Paths
repo_root = Path(__file__).resolve().parent.parent
reports_dir = repo_root / "library" / "edison_reports"
tables_dir = reports_dir / "tables"
reports_index_file = reports_dir / "reports_index.json"

# Ensure directories exist
reports_dir.mkdir(parents=True, exist_ok=True)
tables_dir.mkdir(parents=True, exist_ok=True)

def get_credit_balance() -> Optional[Dict]:
    """Get current credit balance from Edison API."""
    try:
        # Note: This is a placeholder - actual API endpoint may vary
        # Check Edison documentation for correct balance endpoint
        logging.info("Fetching credit balance...")
        # For now, return None - this would need actual API call
        return None
    except Exception as e:
        logging.error(f"Failed to get credit balance: {e}")
        return None

def extract_markdown_tables(text: str) -> List[Dict]:
    """
    Extract markdown tables from text.
    
    Returns list of dicts with:
    - table_markdown: original markdown
    - table_data: parsed data as list of lists
    """
    tables = []
    
    # Regex to match markdown tables
    # Matches table header, separator, and rows
    table_pattern = r'(\|[^\n]+\|(?:\n\|[-:\s|]+\|)(?:\n\|[^\n]+\|)*)'
    
    matches = re.finditer(table_pattern, text, re.MULTILINE)
    
    for idx, match in enumerate(matches):
        table_md = match.group(1)
        
        # Parse table into data
        lines = [line.strip() for line in table_md.split('\n') if line.strip()]
        if len(lines) < 3:  # Need header, separator, at least one row
            continue
        
        # Parse header
        header = [cell.strip() for cell in lines[0].split('|')[1:-1]]
        
        # Parse rows (skip separator line)
        rows = []
        for line in lines[2:]:
            row = [cell.strip() for cell in line.split('|')[1:-1]]
            rows.append(row)
        
        tables.append({
            'table_markdown': table_md,
            'table_data': [header] + rows,
            'table_number': idx + 1
        })
    
    return tables

def table_to_csv(table_data: List[List[str]]) -> str:
    """Convert table data to CSV format."""
    import csv
    import io
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(table_data)
    return output.getvalue()

def parse_citations_from_answer(formatted_answer: str) -> List[Dict]:
    """
    Extract citations from Eddie's formatted answer.
    
    Returns list of dicts with:
    - citation_number: int
    - text: str (full citation text)
    - doi: Optional[str]
    - arxiv_id: Optional[str]
    - title: Optional[str]
    """
    citations = []
    
    # Try to find numbered references section
    # Pattern: [1] Author et al. (Year). Title. Journal. DOI: xxx
    ref_pattern = r'\[(\d+)\]\s*(.+?)(?=\[\d+\]|$)'
    
    matches = re.finditer(ref_pattern, formatted_answer, re.DOTALL)
    
    for match in matches:
        citation_num = int(match.group(1))
        citation_text = match.group(2).strip()
        
        # Extract DOI
        doi_match = re.search(r'doi:?\s*([10]\.\d+/[^\s]+)', citation_text, re.IGNORECASE)
        dois = doi_match.group(1) if doi_match else None
        
        # Extract ArXiv ID
        arxiv_match = re.search(r'arxiv:?\s*(\d+\.\d+)', citation_text, re.IGNORECASE)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None
        
        # Try to extract title (often in quotes or after year)
        title_match = re.search(r'["""](.*?)["""]', citation_text)
        title = title_match.group(1) if title_match else None
        
        citations.append({
            'citation_number': citation_num,
            'text': citation_text,
            'doi': doi,
            'arxiv_id': arxiv_id,
            'title': title
        })
    
    return citations

def save_report(query: str, response, citations: List[Dict], tables: List[Dict]) -> Path:
    """Save Edison report as markdown with tables and update index."""
    
    timestamp = datetime.now()
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
    
    # Create slug from query
    query_slug = re.sub(r'[^\w\s-]', '', query.lower())
    query_slug = re.sub(r'[-\s]+', '_', query_slug)[:50]
    
    report_filename = f"{timestamp_str}_{query_slug}.md"
    report_path = reports_dir / report_filename
    
    # Save tables as CSV
    table_files = []
    for table in tables:
        table_filename = f"{timestamp_str}_{query_slug}_table{table['table_number']}.csv"
        table_path = tables_dir / table_filename
        
        csv_content = table_to_csv(table['table_data'])
        table_path.write_text(csv_content)
        table_files.append(table_filename)
        logging.info(f"Saved table to: {table_path}")
    
    # Build report markdown
    report_md = f"""# Literature Report: {query}

**Generated**: {timestamp.strftime("%Y-%m-%d %H:%M:%S")}
**Agent**: Edison Scientific Literature
**Credits Used**: 1

## Query
>{query}

## Synthesis
{response.get('answer', 'No answer available')}

## Detailed Answer (with citations)
{response.get('formatted_answer', 'No formatted answer available')}

## Cited Papers
"""
    
    if citations:
        for citation in citations:
            report_md += f"- [{citation['citation_number']}] {citation['text']}\n"
            if citation['doi']:
                report_md += f"  - DOI: {citation['doi']}\n"
            if citation['arxiv_id']:
                report_md += f"  - ArXiv: {citation['arxiv_id']}\n"
    else:
        report_md += "No citations parsed.\n"
    
    # Add tables section if any
    if tables:
        report_md += f"\n## Tables\n\n"
        for idx, table_file in enumerate(table_files):
            report_md += f"Table {idx+1}: `tables/{table_file}`\n\n"
            report_md += tables[idx]['table_markdown'] + "\n\n"
    
    report_md += f"""
## Metadata
- Task ID: {response.get('task_id', 'N/A')}
- Success: {response.get('has_successful_answer', False)}
- Papers Found: {len(citations)}
- Tables Found: {len(tables)}
"""
    
    # Write report
    report_path.write_text(report_md)
    logging.info(f"Saved report to: {report_path}")
    
    # Update index
    update_reports_index({
        'query': query,
        'timestamp': timestamp.isoformat(),
        'report_file': report_filename,
        'task_id': response.get('task_id', 'N/A'),
        'papers_found': len(citations),
        'tables_found': len(tables),
        'success': response.get('has_successful_answer', False)
    })
    
    return report_path

def update_reports_index(report_metadata: Dict):
    """Update JSON index of all reports."""
    # Load existing index
    if reports_index_file.exists():
        with open(reports_index_file, 'r') as f:
            index = json.load(f)
    else:
        index = []
    
    # Add new report
    index.append(report_metadata)
    
    # Save index
    with open(reports_index_file, 'w') as f:
        json.dump(index, f, indent=2)
    
    logging.info(f"Updated reports index: {len(index)} reports")

def query_literature(query: str) -> Dict:
    """Query Edison Literature agent."""
    
    # Warn about credit cost
    console.print()
    console.print(Panel(
        "[yellow]⚠️  This will cost 1 credit[/yellow]\n"
        "Edison Scientific Literature agent provides AI-synthesized\n"
        "answers with citations to scientific literature.",
        title="Credit Cost",
        border_style="yellow"
    ))
    
    # Check for confirmation
    console.print("\\nContinue? [Y/n]: ", end="")
    confirm = input().strip().lower()
    if confirm and confirm != 'y':
        console.print("[dim]Cancelled.[/dim]")
        sys.exit(0)
    
    console.print()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("[green]Querying Edison Literature agent...", total=None)
        
        try:
            # Prepare task data per actual API
            task_data = {
                "name": JobNames.LITERATURE,
                "query": query
            }
            
            # Run task until done
            result = edison.run_tasks_until_done(task_data)
            
            progress.update(task, description="[green]✓ Response received")
            
            # Extract response fields (PQATaskResponse)
            return {
                'answer': result.answer,
                'formatted_answer': result.formatted_answer,
                'has_successful_answer': result.has_successful_answer,
                'task_id': getattr(result, 'id', 'N/A')
            }
            
        except Exception as e:
            progress.update(task, description="[red]✗ Error")
            console.print(f"[bold red]Error:[/bold red] {e}")
            logging.error(f"Edison query failed: {e}")
            sys.exit(1)

def add_citations_to_library(citations: List[Dict]):
    """Add selected citations to papis library with PDF fetching."""
    if not citations:
        console.print("[yellow]No citations to add.[/yellow]")
        return
    
    # Prepare fzf input
    fzf_input = []
    for idx, citation in enumerate(citations):
        title = citation.get('title', 'No title')
        doi = citation.get('doi', '')
        arxiv = citation.get('arxiv_id', '')
        
        display = f"{idx}|{citation['citation_number']:3} | {title[:60]:<60} | "
        if arxiv:
            display += f"arXiv:{arxiv}"
        elif doi:
            display += f"DOI:{doi[:30]}"
        
        fzf_input.append(display)
    
    # Invoke fzf
    try:
        fzf_args = [
            'fzf',
            '--multi',
            '--delimiter', '|',
            '--with-nth', '2..',
            '--header', 'TAB: Select papers to add | ENTER: Confirm'
        ]
        
        fzf = subprocess.Popen(fzf_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        stdout, _ = fzf.communicate(input="\n".join(fzf_input))
        
        if not stdout.strip():
            console.print("[dim]No papers selected.[/dim]")
            return
        
        selections = stdout.strip().split('\n')
        
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] fzf not found. Install fzf to use interactive selection.")
        return
    
    # Process selections
    selected_citations = []
    for line in selections:
        if not line: continue
        idx = int(line.split('|')[0].strip())
        selected_citations.append(citations[idx])
    
    console.print(f"\\n[bold]Adding {len(selected_citations)} papers to library...[/bold]")
    
    # Add to papis
    venv_bin = os.path.dirname(sys.executable)
    papis_cmd = os.path.join(venv_bin, "papis")
    papis_config = repo_root / "papis.config"
    
    for citation in selected_citations:
        doi = citation.get('doi')
        arxiv_id = citation.get('arxiv_id')
        
        if not doi and not arxiv_id:
            console.print(f"[yellow]⚠ Skipping:[/yellow] No DOI or ArXiv ID for citation {citation['citation_number']}")
            continue
        
        # Fetch PDF
        pdf_path = fetch_pdf(doi=doi, arxiv_id=arxiv_id)
        
        # Add to papis
        cmd = [papis_cmd, "--config", str(papis_config), "-l", "main", "add", "--batch"]
        
        if arxiv_id:
            cmd.extend(["--from", "arxiv", arxiv_id])
        elif doi:
            cmd.extend(["--from", "doi", doi])
        
        if pdf_path:
            cmd.extend(["--file", str(pdf_path)])
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
            console.print(f"[green]✓[/green] Added: {citation.get('title', 'paper')[:50]}")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to add paper: {e}")
    
    # Update master.bib safely
    try:
        sys.path.insert(0, str(repo_root / "scripts"))
        from utils.sync_bib import sync_master_bib
        if sync_master_bib():
             console.print(f"\\n[green]✓[/green] Updated master.bib")
        else:
             console.print(f"\\n[red]✗[/red] Failed to update master.bib (see logs)")
    except Exception as e:
        console.print(f"\\n[red]✗[/red] Error updating master.bib: {e}")

def main_query(query: str):
    """Main function for Edison literature query."""
    logging.info(f"Starting Edison query: {query}")
    
    # Query Edison
    response = query_literature(query)
    
    # Check success
    if not response.get('has_successful_answer'):
        console.print("[yellow]⚠ Edison could not find a satisfactory answer.[/yellow]")
    
    # Display answer
    console.print()
    console.print(Panel(
        Markdown(response.get('answer', 'No answer')),
        title="[bold green]Edison Synthesis[/bold green]",
        border_style="green"
    ))
    
    # Extract citations and tables
    citations = parse_citations_from_answer(response.get('formatted_answer', ''))
    tables = extract_markdown_tables(response.get('formatted_answer', ''))
    
    console.print(f"\\n[dim]Found {len(citations)} citations and {len(tables)} tables[/dim]")
    
    # Save report
    report_path = save_report(query, response, citations, tables)
    console.print(f"[green]✓[/green] Report saved to: [cyan]{report_path.relative_to(repo_root)}[/cyan]")
    
    if tables:
        console.print(f"[green]✓[/green] {len(tables)} tables exported to CSV in [cyan]library/edison_reports/tables/[/cyan]")
    
    # Ask to add papers
    if citations:
        console.print(f"\\nWould you like to add cited papers to your library? [y/N]: ", end="")
        add_papers = input().strip().lower()
        if add_papers == 'y':
            add_citations_to_library(citations)
    
    console.print("\\n[bold green]✓ Complete![/bold green]")

if __name__ == "__main__":
    logging.info(f"Edison search script started with args: {sys.argv}")
    
    # Handle v2 subcommands
    if len(sys.argv) >= 2:
        if sys.argv[1] == '--list':
            # List all reports
            if not reports_index_file.exists():
                console.print("[yellow]No reports found.[/yellow]")
                console.print(f"Reports will be saved to: {reports_dir}")
                sys.exit(0)
            
            with open(reports_index_file, 'r') as f:
                index = json.load(f)
            
            if not index:
                console.print("[yellow]No reports found.[/yellow]")
                sys.exit(0)
            
            console.print(f"[bold]Edison Literature Reports ({len(index)} total)[/bold]\n")
            
            # Prepare for fzf
            fzf_input = []
            for idx, report in enumerate(index):
                timestamp = datetime.fromisoformat(report['timestamp']) 
                time_str = timestamp.strftime("%Y-%m-%d %H:%M")
                papers = report.get('papers_found', 0)
                tables = report.get('tables_found', 0)
                query = report['query'][:60]
                
                display = f"{idx}|{time_str} | {papers:2}p {tables:1}t | {query}"
                fzf_input.append(display)
            
            # Use fzf to select report
            try:
                fzf = subprocess.Popen(
                    ['fzf', '--header', 'Select report to view | ENTER: Open'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True
                )
                stdout, _ = fzf.communicate(input="\n".join(fzf_input))
                
                if stdout.strip():
                    idx = int(stdout.split('|')[0])
                    report = index[idx]
                    report_path = reports_dir / report['report_file']
                    
                    # Display report
                    if report_path.exists():
                        content = report_path.read_text()
                        console.print(Markdown(content))
                    else:
                        console.print(f"[red]Report file not found:[/red] {report['report_file']}")
                        
            except FileNotFoundError:
                console.print("[bold red]Error:[/bold red] fzf not found")
                # Fallback: just list them
                for idx, report in enumerate(index):
                    console.print(f"{idx}: {report['query']} ({report['timestamp']})")
            
            sys.exit(0)
            
        elif sys.argv[1] == '--show':
            # Show specific report by ID
            if len(sys.argv) < 3:
                console.print("[red]Usage:[/red] research edison show <id>")
                sys.exit(1)
            
            try:
                report_id = int(sys.argv[2])
            except ValueError:
                console.print(f"[red]Invalid report ID:[/red] {sys.argv[2]}")
                sys.exit(1)
            
            if not reports_index_file.exists():
                console.print("[yellow]No reports found.[/yellow]")
                sys.exit(0)
            
            with open(reports_index_file, 'r') as f:
                index = json.load(f)
            
            if report_id < 0 or report_id >= len(index):
                console.print(f"[red]Report ID out of range.[/red] Valid: 0-{len(index)-1}")
                sys.exit(1)
            
            report = index[report_id]
            report_path = reports_dir / report['report_file']
            
            if report_path.exists():
                content = report_path.read_text()
                console.print(Markdown(content))
            else:
                console.print(f"[red]Report file not found:[/red] {report['report_file']}")
            
            sys.exit(0)
            
        elif sys.argv[1] == '--cache':
            # Check if query has been cached
            if len(sys.argv) < 3:
                console.print("[red]Usage:[/red] research edison cache <query>")
                sys.exit(1)
            
            query = " ".join(sys.argv[2:])
            
            if not reports_index_file.exists():
                console.print(f"[yellow]Query not cached:[/yellow] {query}")
                sys.exit(0)
            
            with open(reports_index_file, 'r') as f:
                index = json.load(f)
            
            # Fuzzy match queries (case-insensitive)
            matches = []
            query_lower = query.lower()
            for idx, report in enumerate(index):
                if query_lower in report['query'].lower():
                    matches.append((idx, report))
            
            if not matches:
                console.print(f"[yellow]Query not cached:[/yellow] {query}")
                console.print("Run query to generate new report.")
            else:
                console.print(f"[green]Found {len(matches)} cached report(s):[/green]\n")
                for idx, report in matches:
                    timestamp = datetime.fromisoformat(report['timestamp'])
                    time_str = timestamp.strftime("%Y-%m-%d %H:%M")
                    console.print(f"  [{idx}] {time_str}: {report['query']}")
                console.print(f"\nView with: [cyan]research edison show <id>[/cyan]")
            
            sys.exit(0)
            
        elif sys.argv[1] == '--credits':
            console.print("[yellow]ℹ️  Credit balance feature not yet implemented[/yellow]")
            console.print("Edison API doesn't provide a balance endpoint yet.")
            console.print("Monitor your usage at: https://platform.edisonscientific.com")
            sys.exit(0)
    
    if len(sys.argv) < 2:
        console.print("Usage: research edison <query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    
    # Safeguard against accidental flag processing as query
    if query.startswith("-"):
        console.print(f"[bold red]Invalid query:[/bold red] {query}")
        console.print("Usage: research edison <query>")
        sys.exit(1)

    main_query(query)
