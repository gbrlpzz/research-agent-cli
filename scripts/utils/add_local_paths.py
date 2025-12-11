#!/usr/bin/env python3
"""
Add local file paths to all papis library entries.

This script adds 'file' and 'localdata' fields to all documents in the papis library:
- 'file': Path to the PDF file (BibTeX standard field)
- 'localdata': Path to the info.yaml file (custom field for metadata access)

Usage:
    python scripts/utils/add_local_paths.py [--dry-run]
"""

import sys
import yaml
import logging
from pathlib import Path
from typing import Optional, Tuple
import argparse

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "library"


def find_pdf_in_folder(folder_path: Path) -> Optional[Path]:
    """
    Find the first PDF file in a folder.
    
    Args:
        folder_path: Path to the document folder
        
    Returns:
        Path to PDF file if found, None otherwise
    """
    pdf_files = list(folder_path.glob("*.pdf"))
    if pdf_files:
        return pdf_files[0]  # Return first PDF found
    return None


def load_info_yaml(yaml_path: Path) -> Optional[dict]:
    """Load and parse info.yaml file."""
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Failed to load {yaml_path}: {e}")
        return None


def save_info_yaml(yaml_path: Path, data: dict) -> bool:
    """Save data to info.yaml file."""
    try:
        with open(yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True
    except Exception as e:
        logging.error(f"Failed to save {yaml_path}: {e}")
        return False


def update_document_paths(doc_folder: Path, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Update a single document's info.yaml with local file paths.
    
    Args:
        doc_folder: Path to the document folder
        dry_run: If True, don't actually write changes
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    yaml_path = doc_folder / "info.yaml"
    
    if not yaml_path.exists():
        return False, f"No info.yaml found in {doc_folder.name}"
    
    # Load existing data
    data = load_info_yaml(yaml_path)
    if data is None:
        return False, f"Failed to load {yaml_path}"
    
    # Track if we made any changes
    changes_made = False
    changes_desc = []
    
    # Add PDF path to 'file' field
    pdf_path = find_pdf_in_folder(doc_folder)
    if pdf_path:
        # BibTeX 'file' field format: path:description:type
        # Many tools accept just the path, but we'll use the standard format
        file_value = f"{pdf_path}:PDF:application/pdf"
        
        if 'file' not in data or data['file'] != file_value:
            data['file'] = file_value
            changes_made = True
            changes_desc.append(f"Added PDF: {pdf_path.name}")
    else:
        # No PDF found, but we can still add the YAML path
        changes_desc.append("No PDF found")
    
    # Add info.yaml path to 'localdata' field
    localdata_value = str(yaml_path.absolute())
    if 'localdata' not in data or data['localdata'] != localdata_value:
        data['localdata'] = localdata_value
        changes_made = True
        changes_desc.append(f"Added YAML path")
    
    # Save if changes were made
    if changes_made:
        if not dry_run:
            if save_info_yaml(yaml_path, data):
                return True, f"{doc_folder.name}: {', '.join(changes_desc)}"
            else:
                return False, f"{doc_folder.name}: Failed to save changes"
        else:
            return True, f"[DRY RUN] {doc_folder.name}: {', '.join(changes_desc)}"
    else:
        return True, f"{doc_folder.name}: Already up-to-date"


def process_all_documents(dry_run: bool = False) -> Tuple[int, int, int]:
    """
    Process all documents in the library.
    
    Args:
        dry_run: If True, don't actually write changes
        
    Returns:
        Tuple of (total, success, failed) counts
    """
    if not LIBRARY_DIR.exists():
        logging.error(f"Library directory not found: {LIBRARY_DIR}")
        return 0, 0, 0
    
    # Get all subdirectories (excluding hidden dirs)
    doc_folders = [d for d in LIBRARY_DIR.iterdir() 
                   if d.is_dir() and not d.name.startswith('.')]
    
    total = len(doc_folders)
    success = 0
    failed = 0
    
    logging.info(f"Processing {total} document folders...")
    if dry_run:
        logging.info("DRY RUN MODE - No changes will be saved")
    
    for doc_folder in doc_folders:
        ok, msg = update_document_paths(doc_folder, dry_run=dry_run)
        if ok:
            success += 1
            logging.info(f"✓ {msg}")
        else:
            failed += 1
            logging.warning(f"✗ {msg}")
    
    return total, success, failed


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Add local file paths to all papis library entries"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Show what would be changed without actually modifying files"
    )
    args = parser.parse_args()
    
    logging.info("=" * 60)
    logging.info("Adding local file paths to papis library")
    logging.info("=" * 60)
    
    total, success, failed = process_all_documents(dry_run=args.dry_run)
    
    logging.info("=" * 60)
    logging.info(f"Total documents: {total}")
    logging.info(f"Successfully updated: {success}")
    logging.info(f"Failed: {failed}")
    logging.info("=" * 60)
    
    if not args.dry_run and success > 0:
        logging.info("\nNext steps:")
        logging.info("1. Run: python scripts/utils/sync_bib.py")
        logging.info("2. This will regenerate master.bib with the new file paths")
    
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
