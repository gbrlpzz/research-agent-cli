#!/usr/bin/env python3
"""
Enhanced master.bib sync that adds local file paths.

This script:
1. Exports bibliography from papis
2. Post-processes to add 'file' and 'localdata' fields
3. Safely replaces master.bib with atomic write
"""

import sys
import subprocess
import logging
import yaml
from pathlib import Path
import tempfile
import shutil
import re

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PAPIS_CMD = REPO_ROOT / ".venv" / "bin" / "papis"
MASTER_BIB = REPO_ROOT / "master.bib"
PAPIS_CONFIG = REPO_ROOT / "papis.config"
LIBRARY_DIR = REPO_ROOT / "library"


def get_document_paths(doc_folder: Path) -> dict:
    """
    Get file paths for a document folder.
    
    Returns dict with 'pdf' and 'yaml' keys (values may be None).
    """
    yaml_path = doc_folder / "info.yaml"
    
    if not yaml_path.exists():
        return {'pdf': None, 'yaml': None}
    
    # Find PDF
    pdf_files = list(doc_folder.glob("*.pdf"))
    pdf_path = pdf_files[0] if pdf_files else None
    
    return {
        'pdf': pdf_path,
        'yaml': yaml_path
    }


def get_citation_key_from_yaml(yaml_path: Path) -> str:
    """Extract the citation key that papis would generate from info.yaml."""
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        # Papis generates keys like: {title_words}_{author_last}_{year}
        # We'll extract from the folder name which matches the key pattern
        folder_name = yaml_path.parent.name
        
        # Load folder to key mapping
        return folder_name, data
    except Exception as e:
        logging.error(f"Failed to load {yaml_path}: {e}")
        return None, None


def create_folder_to_key_mapping() -> dict:
    """
    Create mapping from papis folder hash to citation key.
    
    Returns dict: {folder_name: (citation_key, pdf_path, yaml_path)}
    """
    mapping = {}
    
    if not LIBRARY_DIR.exists():
        return mapping
    
    # Get all document folders
    doc_folders = [d for d in LIBRARY_DIR.iterdir() 
                   if d.is_dir() and not d.name.startswith('.')]
    
    for doc_folder in doc_folders:
        yaml_path = doc_folder / "info.yaml"
        if not yaml_path.exists():
            continue
        
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            # Extract components for key generation (simplified)
            title = data.get('title', '').strip()
            authors = data.get('author_list', data.get('author', []))
            year = data.get('year', '')
            
            if isinstance(authors, str):
                author_last = authors.split()[-1] if authors else 'Unknown'
            elif isinstance(authors, list) and len(authors) > 0:
                if isinstance(authors[0], dict):
                    author_last = authors[0].get('family', authors[0].get('surname', 'Unknown'))
                else:
                    author_last = str(authors[0]).split()[-1]
            else:
                author_last = 'Unknown'
            
            # Create simplified key (papis uses a more complex algorithm)
            # We'll match against what's actually in the bibtex
            # For now, just store the folder name as identifier
            
            # Get file paths
            paths = get_document_paths(doc_folder)
            
            mapping[doc_folder.name] = {
                'title': title,
                'author': author_last,
                'year': str(year),
                'pdf': paths['pdf'],
                'yaml': paths['yaml']
            }
            
        except Exception as e:
            logging.warning(f"Failed to process {doc_folder.name}: {e}")
            continue
    
    return mapping


def add_file_paths_to_bibtex(bibtex_content: str) -> str:
    """
    Post-process BibTeX content to add file and localdata fields.
    
    This parses the BibTeX, matches entries to folders, and adds fields.
    """
    # Create folder mapping
    folder_mapping = create_folder_to_key_mapping()
    
    # Match title/author/year to find the right folder
    # Split into entries
    entries = re.split(r'(@\w+\{[^,]+,)', bibtex_content)
    
    result = []
    for i, part in enumerate(entries):
        result.append(part)
        
        # Check if this is an entry header
        if part.strip().startswith('@'):
            # Get the entry body (next part)
            if i + 1 < len(entries):
                entry_body = entries[i + 1]
                
                # Extract title, author, year from entry
                title_match = re.search(r'title\s*=\s*\{([^}]+)\}', entry_body, re.IGNORECASE)
                author_match = re.search(r'author\s*=\s*\{([^}]+)\}', entry_body, re.IGNORECASE)
                year_match = re.search(r'year\s*=\s*\{([^}]+)\}', entry_body, re.IGNORECASE)
                
                if title_match and author_match and year_match:
                    title = title_match.group(1).strip()
                    author_str = author_match.group(1).strip()
                    year = year_match.group(1).strip()
                    
                    # Find matching folder
                    for folder_name, data in folder_mapping.items():
                        # Simple matching - could be improved
                        # Add None checks
                        data_author = data.get('author', '')
                        data_title = data.get('title', '')
                        data_year = str(data.get('year', ''))
                        
                        if (data_year == year and 
                            data_author and author_str and data_author.lower() in author_str.lower() and
                            data_title and len(title) > 10 and title[:20].lower() in data_title.lower()):

                            
                            # Add file fields before the closing brace
                            additions = []
                            if data['pdf']:
                                file_value = f"{data['pdf']}:PDF:application/pdf"
                                additions.append(f"  file = {{{file_value}}},\n")
                            if data['yaml']:
                                additions.append(f"  localdata = {{{data['yaml']}}},\n")
                            
                            if additions:
                                # Find last } in entry_body
                                last_brace = entry_body.rfind('}')
                                if last_brace != -1:
                                    modified_body = (entry_body[:last_brace] + 
                                                   ''.join(additions) + 
                                                   entry_body[last_brace:])
                                    # Replace in result (it will be added next iteration)
                                    entries[i + 1] = modified_body
                            break
    
    return ''.join(entries)


def sync_master_bib():
    """
    Safely regenerates master.bib by exporting to a temp file first,
    then adding local file paths.
    """
    logging.info("Starting master.bib sync...")
    
    if not PAPIS_CMD.exists():
        logging.error(f"Papis executable not found at {PAPIS_CMD}")
        return False

    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.bib') as tmp_file:
        tmp_path = Path(tmp_file.name)
    
    try:
        # Export to temp file
        cmd = [
            str(PAPIS_CMD),
            "--config", str(PAPIS_CONFIG),
            "-l", "main",
            "export",
            "--all",
            "-f", "bibtex",
            "-o", str(tmp_path)
        ]
        
        logging.debug(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            logging.error(f"Papis export failed: {result.stderr}")
            if tmp_path.exists():
                tmp_path.unlink()
            return False
            
        # Check if the file is not empty
        if tmp_path.stat().st_size == 0:
            logging.warning("Exported bibliography is empty. Aborting sync.")
            tmp_path.unlink()
            return False
            
        # Parse output to ensure it looks like bibtex (basic check)
        content = tmp_path.read_text()
        if "@" not in content:
            logging.warning("Exported content does not appear to contain BibTeX entries. Aborting.")
            tmp_path.unlink()
            return False

        # Post-process to add file paths
        logging.info("Adding local file paths to bibliography entries...")
        enhanced_content = add_file_paths_to_bibtex(content)
        
        # Write enhanced content back
        tmp_path.write_text(enhanced_content)

        # Create backup if master.bib exists
        if MASTER_BIB.exists():
            import time
            timestamp = int(time.time())
            
            # Create backup directory if it doesn't exist
            backup_dir = REPO_ROOT / ".backups"
            backup_dir.mkdir(exist_ok=True)
            
            backup_path = backup_dir / f"master.bib.bak.{timestamp}"
            shutil.copy2(str(MASTER_BIB), str(backup_path))
            logging.info(f"Created backup: {backup_path}")
            
            # Clean up old backups (keep last 5)
            backups = sorted(backup_dir.glob("master.bib.bak.*"), key=lambda p: p.stat().st_mtime)
            while len(backups) > 5:
                oldest = backups.pop(0)
                try:
                    oldest.unlink()
                    logging.info(f"Removed old backup: {oldest}")
                except Exception as e:
                    logging.warning(f"Failed to remove old backup {oldest}: {e}")

        # Atomic replacement
        shutil.move(str(tmp_path), str(MASTER_BIB))
        logging.info(f"Successfully updated {MASTER_BIB} ({MASTER_BIB.stat().st_size} bytes)")
        return True

    except Exception as e:
        logging.error(f"Exception during sync: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False

if __name__ == "__main__":
    success = sync_master_bib()
    sys.exit(0 if success else 1)
