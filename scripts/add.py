#!/usr/bin/env python3
"""
Quick add tool - add paper from DOI/arXiv in clipboard or argument.
Usage: python add.py [identifier]

If no argument, reads from clipboard.
Detects DOI (10.xxxx/...) or arXiv ID (xxxx.xxxxx) automatically.
"""

import sys
import subprocess
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAPIS_CMD = REPO_ROOT / ".venv" / "bin" / "papis"

def get_clipboard():
    """Get text from macOS clipboard."""
    try:
        result = subprocess.run(['pbpaste'], capture_output=True, text=True)
        return result.stdout.strip()
    except:
        return ""

def detect_identifier(text):
    """Detect DOI or arXiv ID from text."""
    # DOI pattern: 10.xxxx/anything
    doi_match = re.search(r'(10\.\d{4,}/[^\s]+)', text)
    if doi_match:
        return 'doi', doi_match.group(1)
    
    # arXiv pattern: xxxx.xxxxx or arXiv:xxxx.xxxxx
    arxiv_match = re.search(r'(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)', text, re.IGNORECASE)
    if arxiv_match:
        return 'arxiv', arxiv_match.group(1)
    
    return None, None

def add_paper(source, identifier):
    """Add paper using papis."""
    cmd = [str(PAPIS_CMD), "-l", "main", "add", "--batch", "--from", source, identifier]
    
    print(f"Adding {source}:{identifier}...")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print("✓ Added successfully")
            
            # Export to master.bib (delete first to prevent duplicates)
            master_bib = REPO_ROOT / "master.bib"
            if master_bib.exists():
                master_bib.unlink()
            export_cmd = [str(PAPIS_CMD), "-l", "main", "export", "--all", "-f", "bibtex", "-o", str(master_bib)]
            subprocess.run(export_cmd, capture_output=True, text=True, timeout=30)
            print("✓ Updated master.bib")
        else:
            print(f"✗ Failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        print("✗ Timeout")
    except Exception as e:
        print(f"✗ Error: {e}")

def main():
    # Get identifier from args or clipboard
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = get_clipboard()
        if text:
            print(f"From clipboard: {text[:50]}...")
    
    if not text:
        print("Usage: research add [DOI or arXiv ID]")
        print("       Or copy a DOI/arXiv ID and run: research add")
        return
    
    source, identifier = detect_identifier(text)
    
    if not source:
        print(f"Could not detect DOI or arXiv ID in: {text[:50]}")
        return
    
    add_paper(source, identifier)

if __name__ == "__main__":
    main()
