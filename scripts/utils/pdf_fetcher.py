"""
PDF Fetcher Utility
Automatically fetches PDFs for papers from DOI/ArXiv IDs.
"""
import requests
import tempfile
import logging
from pathlib import Path
from typing import Optional
from rich.console import Console

console = Console()
logging.basicConfig(
    filename='debug_research.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def fetch_pdf_from_arxiv(arxiv_id: str) -> Optional[Path]:
    """
    Fetch PDF directly from ArXiv.
    
    Args:
        arxiv_id: ArXiv ID (e.g., "2301.00001")
    
    Returns:
        Path to downloaded PDF or None if failed
    """
    try:
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        logging.info(f"Fetching PDF from ArXiv: {url}")
        
        response = requests.get(url, timeout=30, stream=True)
        response.raise_for_status()
        
        # Create temp file
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        
        # Download in chunks
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                temp_pdf.write(chunk)
        
        temp_pdf.close()
        logging.info(f"PDF downloaded to: {temp_pdf.name}")
        return Path(temp_pdf.name)
        
    except Exception as e:
        logging.error(f"Failed to fetch PDF from ArXiv {arxiv_id}: {e}")
        return None

def fetch_pdf_from_unpaywall(doi: str, email: str = "research@example.com") -> Optional[Path]:
    """
    Fetch PDF URL from Unpaywall API (free, legal PDF access).
    
    Args:
        doi: DOI of the paper
        email: Email for Unpaywall API (required)
    
    Returns:
        Path to downloaded PDF or None if not available
    """
    try:
        # Query Unpaywall API
        api_url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        logging.info(f"Querying Unpaywall API: {api_url}")
        
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Check for open access PDF
        best_oa_location = data.get('best_oa_location')
        if not best_oa_location:
            logging.info(f"No open access PDF found for DOI: {doi}")
            return None
        
        pdf_url = best_oa_location.get('url_for_pdf')
        if not pdf_url:
            logging.info(f"Open access location found but no PDF URL for DOI: {doi}")
            return None
        
        logging.info(f"Found PDF URL via Unpaywall: {pdf_url}")
        
        # Download the PDF
        pdf_response = requests.get(pdf_url, timeout=30, stream=True)
        pdf_response.raise_for_status()
        
        # Create temp file
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        
        # Download in chunks
        for chunk in pdf_response.iter_content(chunk_size=8192):
            if chunk:
                temp_pdf.write(chunk)
        
        temp_pdf.close()
        logging.info(f"PDF downloaded to: {temp_pdf.name}")
        return Path(temp_pdf.name)
        
    except Exception as e:
        logging.error(f"Failed to fetch PDF from Unpaywall for DOI {doi}: {e}")
        return None

# Graceful external tool import for private sources
try:
    import sys
    from pathlib import Path
    # Ensure tools module is importable
    scripts_path = Path(__file__).resolve().parent.parent
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))
    from tools.external import fetch_pdf_private, PRIVATE_SOURCES_AVAILABLE
except ImportError:
    fetch_pdf_private = None
    PRIVATE_SOURCES_AVAILABLE = False


def fetch_pdf(doi: Optional[str] = None, arxiv_id: Optional[str] = None) -> Optional[Path]:
    """
    Attempt to fetch PDF from multiple sources.
    
    Tries in order:
    1. ArXiv (if arxiv_id provided)
    2. Unpaywall (if DOI provided)
    3. Private sources (if DOI provided and available, fallback for paywalled papers)
    
    Args:
        doi: DOI of the paper
        arxiv_id: ArXiv ID of the paper
   
    Returns:
        Path to downloaded PDF or None if not available
    """
    # Try ArXiv first (most reliable)
    if arxiv_id:
        console.print(f"[dim]Attempting PDF download from ArXiv...[/dim]")
        pdf_path = fetch_pdf_from_arxiv(arxiv_id)
        if pdf_path:
            console.print(f"[green]✓[/green] PDF downloaded from ArXiv")
            return pdf_path
    
    # Try Unpaywall for DOI (legal open access)
    if doi:
        console.print(f"[dim]Attempting PDF download via Unpaywall...[/dim]")
        pdf_path = fetch_pdf_from_unpaywall(doi)
        if pdf_path:
            console.print(f"[green]✓[/green] PDF downloaded via Unpaywall")
            return pdf_path
    
    # Fallback to private sources for paywalled papers (if available)
    if doi and PRIVATE_SOURCES_AVAILABLE and fetch_pdf_private:
        console.print(f"[dim]Attempting PDF download via private sources...[/dim]")
        pdf_path = fetch_pdf_private(doi)
        if pdf_path:
            console.print(f"[green]✓[/green] PDF downloaded via private sources")
            return pdf_path
    elif doi and not PRIVATE_SOURCES_AVAILABLE:
        console.print(f"[dim]Private sources not available (external tool not installed)[/dim]")
    
    console.print(f"[yellow]⚠[/yellow] No PDF available from any source")
    logging.info("PDF fetch failed: no sources available")
    return None


if __name__ == "__main__":
    # Test the PDF fetcher
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python pdf_fetcher.py <arxiv_id or doi>")
        sys.exit(1)
    
    identifier = sys.argv[1]
    
    # Detect if it's ArXiv or DOI
    if identifier.startswith('10.'):
        # Likely a DOI
        pdf = fetch_pdf(doi=identifier)
    else:
        # Assume ArXiv
        pdf = fetch_pdf(arxiv_id=identifier)
    
    if pdf:
        print(f"PDF saved to: {pdf}")
    else:
        print("Failed to fetch PDF")
