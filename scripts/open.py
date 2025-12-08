#!/usr/bin/env python3
"""
Open paper in browser - search library and open paper URL.
Usage: python open.py [query]

Opens the paper's URL (DOI, arXiv, or semantic scholar) in the default browser.
"""

import sys
import subprocess
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "library"

def load_entries():
    """Load all entries from info.yaml files in library."""
    entries = []
    
    if not LIBRARY_DIR.exists():
        return entries
    
    for folder in LIBRARY_DIR.iterdir():
        if not folder.is_dir():
            continue
        
        info_file = folder / "info.yaml"
        if not info_file.exists():
            continue
        
        try:
            with open(info_file) as f:
                data = yaml.safe_load(f)
            
            if data:
                data['_folder'] = folder
                entries.append(data)
        except Exception:
            continue
    
    return entries

def get_url(entry):
    """Get best URL for a paper entry."""
    # Priority: DOI > arXiv > url > doc_url
    if entry.get('doi'):
        return f"https://doi.org/{entry['doi']}"
    if entry.get('eprint'):  # arXiv
        return f"https://arxiv.org/abs/{entry['eprint']}"
    if entry.get('url'):
        return entry['url']
    if entry.get('doc_url'):
        return entry['doc_url']
    return None

def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    
    entries = load_entries()
    
    if not entries:
        print("No entries in library")
        return
    
    # Format for fzf display
    fzf_lines = []
    for e in entries:
        key = e.get('ref', 'unknown')
        author = e.get('author', 'Unknown')[:35]
        title = e.get('title', 'Untitled')[:50]
        year = e.get('year', '????')
        url = get_url(e) or ''
        
        # Include URL in hidden field for reference
        line = f"{key}\t{url}\t{year}\t{author}\t{title}"
        fzf_lines.append(line)
    
    # Build fzf command
    fzf_cmd = ['fzf', '--delimiter', '\t',
         '--with-nth', '2..',
         '--header', 'Select paper to open in browser | q: Quit',
         '--bind', 'q:abort',
         '--preview', 'echo "URL: {2}"',
         '--preview-window', 'up:1']
    
    if query:
        fzf_cmd.extend(['-q', query])
    
    try:
        fzf = subprocess.Popen(
            fzf_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True
        )
        stdout, _ = fzf.communicate(input="\n".join(fzf_lines))
    except FileNotFoundError:
        print("Error: fzf not found")
        return
    
    if not stdout.strip():
        return
    
    # Get URL from selected line
    parts = stdout.strip().split('\t')
    if len(parts) >= 2:
        url = parts[1]
        if url:
            print(f"Opening: {url}")
            subprocess.run(['open', url])
        else:
            print("No URL available for this paper")

if __name__ == "__main__":
    main()
