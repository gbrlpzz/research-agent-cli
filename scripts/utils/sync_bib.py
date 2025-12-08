import sys
import subprocess
import logging
from pathlib import Path
import tempfile
import shutil

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PAPIS_CMD = REPO_ROOT / ".venv" / "bin" / "papis"
MASTER_BIB = REPO_ROOT / "master.bib"
PAPIS_CONFIG = REPO_ROOT / "papis.config"

def sync_master_bib():
    """
    Safely regenerates master.bib by exporting to a temp file first.
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
