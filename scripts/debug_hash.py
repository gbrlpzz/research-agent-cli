
import pickle
import hashlib
from pathlib import Path
from paperqa import Docs

def compute_md5(file_path):
    return hashlib.md5(file_path.read_bytes()).hexdigest()

def debug_hash():
    library_path = Path("library")
    pkl_path = library_path / ".qa_docs.pkl"
    
    # Load Docs
    with open(pkl_path, 'rb') as f:
        docs = pickle.load(f)
        
    print(f"Loaded {len(docs.docnames)} docs.")
    
    # Collect Remote Hashes (dockeys)
    remote_hashes = set()
    if hasattr(docs, 'docs'):
        for k, v in docs.docs.items():
            if hasattr(v, 'dockey'):
                remote_hashes.add(v.dockey)
    
    print(f"Collected {len(remote_hashes)} remote hash keys.")
    if len(remote_hashes) > 0:
        print(f"Example Remote Hash: {list(remote_hashes)[0]}")

    # Scan Local Files
    pdf_files = list(library_path.rglob("*.pdf"))
    print(f"Scanning {len(pdf_files)} local PDFs...")
    
    matches = 0
    failures = 0
    
    for pdf in pdf_files: # Check all
        local_hash = compute_md5(pdf)
        if local_hash in remote_hashes:
            matches += 1
            print(f"MATCH: {pdf.name} -> {local_hash}")
        else:
            failures += 1
            print(f"FAIL:  {pdf.name} -> {local_hash} NOT IN REMOTE")
            
    print(f"Summary: {matches} Matches, {failures} Failures")

if __name__ == "__main__":
    debug_hash()
