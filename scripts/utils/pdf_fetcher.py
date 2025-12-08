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

def fetch_pdf_from_scihub(doi: str) -> Optional[Path]:
    """
    Fetch PDF from Sci-Hub using DOI.
    
    Args:
        doi: DOI of the paper
    
    Returns:
        Path to downloaded PDF or None if failed
    """
    import re
    from urllib.parse import urljoin
    
    # List of Sci-Hub mirrors to try
    scihub_mirrors = [
        "https://sci-hub.se",
        "https://sci-hub.st", 
        "https://sci-hub.ru",
        "https://sci-hub.ee",
        "https://sci-hub.wf",
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    for mirror in scihub_mirrors:
        try:
            scihub_url = f"{mirror}/{doi}"
            logging.info(f"Trying Sci-Hub mirror: {scihub_url}")
            
            response = requests.get(scihub_url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                logging.debug(f"Mirror {mirror} returned status {response.status_code}")
                continue
            
            # Parse the response to find the PDF URL
            # Sci-Hub embeds the PDF in an iframe or provides a direct download button
            html_content = response.text
            
            # Try to find PDF URL in various patterns
            pdf_url = None
            
            # Pattern 1: object tag with data attribute (common in 2024 Sci-Hub)
            # <object type="application/pdf" data="/storage/moscow/...pdf#navpanes=0">
            object_match = re.search(r'<object[^>]+data\s*=\s*["\']([^"\'#]+\.pdf)', html_content, re.IGNORECASE)
            if object_match:
                pdf_url = object_match.group(1)
                logging.info(f"Found PDF URL via object data: {pdf_url}")
            
            # Pattern 2: download link with href (Sci-Hub download button)
            # <a href="/download/moscow/.../file.pdf">
            if not pdf_url:
                download_match = re.search(r'href\s*=\s*["\']([^"\']*download[^"\']*\.pdf)["\']', html_content, re.IGNORECASE)
                if download_match:
                    pdf_url = download_match.group(1)
                    logging.info(f"Found PDF URL via download href: {pdf_url}")
            
            # Pattern 3: iframe with PDF src
            if not pdf_url:
                iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']', html_content, re.IGNORECASE)
                if iframe_match:
                    pdf_url = iframe_match.group(1)
                    logging.info(f"Found PDF URL via iframe: {pdf_url}")
            
            # Pattern 4: embed tag
            if not pdf_url:
                embed_match = re.search(r'<embed[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']', html_content, re.IGNORECASE)
                if embed_match:
                    pdf_url = embed_match.group(1)
                    logging.info(f"Found PDF URL via embed: {pdf_url}")
            
            # Pattern 5: any PDF link as last resort
            if not pdf_url:
                pdf_link_match = re.search(r'href=["\']([^"\']+\.pdf)["\']', html_content)
                if pdf_link_match:
                    pdf_url = pdf_link_match.group(1)
                    logging.info(f"Found PDF URL via href: {pdf_url}")
            
            if not pdf_url:
                logging.debug(f"No PDF URL found on {mirror}")
                continue
            
            # Normalize URL
            if pdf_url.startswith('//'):
                pdf_url = 'https:' + pdf_url
            elif not pdf_url.startswith('http'):
                pdf_url = urljoin(mirror, pdf_url)
            
            # Download the PDF
            logging.info(f"Downloading PDF from: {pdf_url}")
            pdf_response = requests.get(pdf_url, headers=headers, timeout=60, stream=True)
            pdf_response.raise_for_status()
            
            # Verify it's actually a PDF
            content_type = pdf_response.headers.get('content-type', '')
            if 'pdf' not in content_type.lower() and not pdf_url.endswith('.pdf'):
                # Check magic bytes
                first_bytes = next(pdf_response.iter_content(chunk_size=4))
                if not first_bytes.startswith(b'%PDF'):
                    logging.debug(f"Response from {pdf_url} is not a PDF")
                    continue
                    
            # Create temp file
            temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            
            # Write first bytes if we consumed them for verification
            if 'first_bytes' in locals():
                temp_pdf.write(first_bytes)
            
            # Download rest in chunks
            for chunk in pdf_response.iter_content(chunk_size=8192):
                if chunk:
                    temp_pdf.write(chunk)
            
            temp_pdf.close()
            
            # Verify the file has content
            if Path(temp_pdf.name).stat().st_size < 1000:
                logging.debug(f"Downloaded file too small, likely not a valid PDF")
                Path(temp_pdf.name).unlink()
                continue
                
            logging.info(f"PDF downloaded from Sci-Hub to: {temp_pdf.name}")
            return Path(temp_pdf.name)
            
        except requests.exceptions.Timeout:
            logging.debug(f"Timeout connecting to {mirror}")
            continue
        except requests.exceptions.RequestException as e:
            logging.debug(f"Request error with {mirror}: {e}")
            continue
        except Exception as e:
            logging.error(f"Unexpected error with Sci-Hub {mirror}: {e}")
            continue
    
    logging.info(f"All Sci-Hub mirrors failed for DOI: {doi}")
    return None


def fetch_pdf(doi: Optional[str] = None, arxiv_id: Optional[str] = None) -> Optional[Path]:
    """
    Attempt to fetch PDF from multiple sources.
    
    Tries in order:
    1. ArXiv (if arxiv_id provided)
    2. Unpaywall (if DOI provided)
    3. Sci-Hub (if DOI provided, fallback for paywalled papers)
    
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
    
    # Fallback to Sci-Hub for paywalled papers
    if doi:
        console.print(f"[dim]Attempting PDF download via Sci-Hub...[/dim]")
        pdf_path = fetch_pdf_from_scihub(doi)
        if pdf_path:
            console.print(f"[green]✓[/green] PDF downloaded via Sci-Hub")
            return pdf_path
    
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
