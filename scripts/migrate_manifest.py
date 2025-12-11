
import pickle
import hashlib
import json
from pathlib import Path
import logging
from rich.console import Console

console = Console()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def compute_md5(file_path):
    try:
        return hashlib.md5(file_path.read_bytes()).hexdigest()
    except Exception:
        return None

def migrate_manifest():
    library_path = Path("library")
    pkl_path = library_path / ".qa_docs.pkl"
    manifest_path = library_path / ".qa_manifest.json"
    
    if not pkl_path.exists():
        console.print("[red]No pickle found. Cannot migrate.[/red]")
        return

    # Load Docs
    try:
        with open(pkl_path, 'rb') as f:
            docs = pickle.load(f)
        console.print(f"[green]Loaded Docs with {len(docs.docs)} entries[/green]")
    except Exception as e:
        console.print(f"[red]Error loading pickle: {e}[/red]")
        return

    # Collect all existing hashes (dockeys)
    indexed_hashes = set()
    if hasattr(docs, 'docs'):
        for d in docs.docs.values():
            if hasattr(d, 'dockey'):
                indexed_hashes.add(d.dockey)
    
    console.print(f"[cyan]Found {len(indexed_hashes)} unique hashes in Index[/cyan]")
    
    # Build Manifest
    manifest = {}
    pdf_files = list(library_path.rglob("*.pdf"))
    
    for pdf in pdf_files:
        file_hash = compute_md5(pdf)
        if file_hash and file_hash in indexed_hashes:
            manifest[pdf.name] = file_hash
            console.print(f"[green]Matched: {pdf.name}[/green]")
        else:
            console.print(f"[yellow]Skipping: {pdf.name} (Not in index)[/yellow]")
            
    # Save Manifest
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
        
    console.print(f"\n[bold green]Migration Complete.[/bold green]")
    console.print(f"Created manifest with {len(manifest)} entries.")

if __name__ == "__main__":
    migrate_manifest()
