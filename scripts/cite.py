#!/usr/bin/env python3
"""
Citation search tool - search master.bib and copy citation keys.
Usage: python cite.py [query]
       python cite.py --preview <index> <temp_file>

Searches across title, author, year, journal, and abstract.
The optional query pre-filters results in fzf.
"""

import sys
import subprocess
import re
import json
import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_BIB = REPO_ROOT / "master.bib"

def parse_bibtex(bib_path):
    """Parse bibtex file and extract entries including abstract."""
    entries = []
    
    if not bib_path.exists():
        return entries
    
    content = bib_path.read_text()
    
    # Match bibtex entries: @type{key, ... }
    pattern = r'@(\w+)\{([^,]+),([^@]*)'
    matches = re.findall(pattern, content, re.DOTALL)
    
    for entry_type, key, fields in matches:
        entry = {'key': key.strip(), 'type': entry_type}
        
        # Extract common fields including abstract
        for field in ['author', 'title', 'year', 'journal', 'doi', 'abstract']:
            field_match = re.search(rf'{field}\s*=\s*\{{([^}}]*)\}}', fields, re.IGNORECASE)
            if field_match:
                entry[field] = field_match.group(1).strip()
        
        entries.append(entry)
    
    return entries

def preview_entry(index, temp_file_path):
    """Print preview for the given index."""
    try:
        with open(temp_file_path, 'r') as f:
            data = json.load(f)
        
        idx = int(index)
        if 0 <= idx < len(data):
            e = data[idx]
            print(f"\nTitle: {e.get('title', 'Unknown')}")
            print(f"Authors: {e.get('author', 'Unknown')}")
            print(f"Year: {e.get('year', '????')}")
            if e.get('journal'):
                print(f"Journal: {e.get('journal')}")
            print("-" * 40)
            print(e.get('abstract') or "No abstract available.")
        else:
            print("Entry not found.")
    except Exception as e:
        print(f"Error: {e}")

def main():
    # Check for preview mode
    if len(sys.argv) >= 4 and sys.argv[1] == '--preview':
        preview_entry(sys.argv[2], sys.argv[3])
        return
    
    # Get optional query from args
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    
    entries = parse_bibtex(MASTER_BIB)
    
    if not entries:
        print("No entries in master.bib")
        return
    
    # Build data for fzf and preview
    fzf_lines = []
    preview_data = []
    
    for idx, e in enumerate(entries):
        key = e['key']
        author = e.get('author', 'Unknown')[:35]
        title = e.get('title', 'Untitled')[:50]
        year = e.get('year', '????')
        
        # Get URL for opening
        doi = e.get('doi', '')
        url = f"https://doi.org/{doi}" if doi else ''
        
        # Format: idx | key | URL | year | author | title
        line = f"{idx}|{key}|{url}|{year}|{author}|{title}"
        fzf_lines.append(line)
        preview_data.append(e)
    
    # Create temp file for preview
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp_file:
        json.dump(preview_data, tmp_file)
        tmp_path = tmp_file.name
    
    try:
        # Preview command
        preview_cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --preview {{1}} "{tmp_path}"'
        
        # Build fzf command
        fzf_cmd = ['fzf', '--multi', '--delimiter', '|',
             '--with-nth', '4..', 
             '--header', 'TAB: Select | o: Open in browser | ENTER: Copy citation',
             '--preview', preview_cmd,
             '--preview-window', 'right:50%:wrap',
             '--bind', 'o:execute-silent(open {3})']
        
        if query:
            fzf_cmd.extend(['-q', query])
        
        # Use fzf for selection
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
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    
    if not stdout.strip():
        return
    
    # Extract selected keys (field 2 is key)
    selected_keys = []
    for line in stdout.strip().split('\n'):
        if line:
            parts = line.split('|')
            if len(parts) >= 2:
                key = parts[1]
                selected_keys.append(f"@{key}")
    
    result = " ".join(selected_keys)
    
    # Copy to clipboard (macOS)
    try:
        subprocess.run(['pbcopy'], input=result, text=True, check=True)
        print(f"Copied to clipboard: {result}")
    except:
        print(f"Citation keys: {result}")

if __name__ == "__main__":
    main()
