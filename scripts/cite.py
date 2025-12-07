#!/usr/bin/env python3
"""
Citation search tool - search master.bib and copy citation keys.
Usage: python cite.py [query]

The optional query pre-filters results in fzf.
"""

import sys
import subprocess
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_BIB = REPO_ROOT / "master.bib"

def parse_bibtex(bib_path):
    """Parse bibtex file and extract entries."""
    entries = []
    
    if not bib_path.exists():
        return entries
    
    content = bib_path.read_text()
    
    # Match bibtex entries: @type{key, ... }
    pattern = r'@(\w+)\{([^,]+),([^@]*)'
    matches = re.findall(pattern, content, re.DOTALL)
    
    for entry_type, key, fields in matches:
        entry = {'key': key.strip(), 'type': entry_type}
        
        # Extract common fields
        for field in ['author', 'title', 'year', 'journal', 'doi']:
            field_match = re.search(rf'{field}\s*=\s*\{{([^}}]*)\}}', fields, re.IGNORECASE)
            if field_match:
                entry[field] = field_match.group(1).strip()
        
        entries.append(entry)
    
    return entries

def main():
    # Get optional query from args
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    
    entries = parse_bibtex(MASTER_BIB)
    
    if not entries:
        print("No entries in master.bib")
        return
    
    # Format for fzf display
    fzf_lines = []
    for e in entries:
        author = e.get('author', 'Unknown')[:30]
        title = e.get('title', 'Untitled')[:50]
        year = e.get('year', '????')
        key = e['key']
        
        line = f"{key}\t{year}\t{author}\t{title}"
        fzf_lines.append(line)
    
    # Build fzf command with optional query
    fzf_cmd = ['fzf', '--multi', '--delimiter', '\t',
         '--with-nth', '2..', 
         '--header', 'Select citations (TAB to multi-select, ENTER to confirm)',
         '--preview', 'echo "Citation key: @{1}"',
         '--preview-window', 'up:1']
    
    if query:
        fzf_cmd.extend(['-q', query])
    
    # Use fzf for selection
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
    
    # Extract selected keys
    selected_keys = []
    for line in stdout.strip().split('\n'):
        if line:
            key = line.split('\t')[0]
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
