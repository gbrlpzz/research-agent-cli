#!/usr/bin/env python3
"""
Backfill PDFs script.

Iterates through the library, identifies entries without a PDF,
and attempts to download them using Sci-Hub and Anna's Archive.
"""

import sys
import os
import yaml
import shutil
from pathlib import Path

# Add scripts to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rich.console import Console
from tools.external.private_sources import fetch_pdf_private, fetch_pdf_annas

console = Console()
LIBRARY_PATH = REPO_ROOT / "library"

def backfill_library():
    console.print("[bold blue]📦 Starting Library Backfill...[/bold blue]")
    
    count_checked = 0
    count_found = 0
    count_missing = 0
    
    # Iterate over all info.yaml files
    for info_file in LIBRARY_PATH.rglob("info.yaml"):
        folder = info_file.parent
        count_checked += 1
        
        # Check if PDF exists
        pdfs = list(folder.glob("*.pdf"))
        
        if pdfs:
            # Already has PDF
            continue
            
        # No PDF found
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
            
        title = data.get('title', 'Unknown')
        ref = data.get('ref', 'unknown')
        doi = data.get('doi')
        
        if not doi:
            console.print(f"[dim]Skipping {ref} (No DOI)[/dim]")
            continue
            
        console.print(f"[yellow]Missing PDF for:[/yellow] {title[:60]}...")
        console.print(f"   [dim]DOI: {doi}[/dim]")
        
        # Attempt fetch
        pdf_path = None
        try:
            # Try private sources (Sci-Hub + Anna's Archive)
            pdf_path = fetch_pdf_private(doi)
            
            if not pdf_path and fetch_pdf_annas:
                 # Explicitly try Anna's if fetch_pdf_private didn't (though it should have)
                 pass 
                 
        except Exception as e:
            console.print(f"   [red]Error fetching: {e}[/red]")
            
        if pdf_path and pdf_path.exists():
            # Success! Move file to library folder
            target_path = folder / f"{ref}.pdf"
            shutil.copy(pdf_path, target_path)
            console.print(f"   [green]✓ Recovered PDF![/green]")
            count_found += 1
            try:
                pdf_path.unlink() # Cleanup temp
            except:
                pass
        else:
            console.print(f"   [red]✗ Could not find PDF[/red]")
            count_missing += 1
            
    console.print("\n[bold]Backfill Complete[/bold]")
    console.print(f"Checked: {count_checked}")
    console.print(f"Recovered: {count_found}")
    console.print(f"Still Missing: {count_missing}")

if __name__ == "__main__":
    backfill_library()
