
import pickle
from pathlib import Path
# from qdrant_client import AsyncQdrantClient # Removed
# But load might need PaperQA imports if class is there? 
from paperqa import Docs

def debug_pickle():
    library_path = Path("library")
    pkl_path = library_path / ".qa_docs.pkl"
    
    print(f"Checking {pkl_path}...")
    if not pkl_path.exists():
        print("Pickle file missing!")
        return

    with open(pkl_path, 'rb') as f:
        docs = pickle.load(f)
    
    print(f"Loaded Docs object with {len(docs.docnames)} docnames.")
    
    print("\nSample Docnames (Pickle):")
    sample_docs = list(docs.docnames)[:5]
    for d in sample_docs:
        print(f" - {d}")
        
    print("\nScanning PDF files in library...")
    pdf_files = list(library_path.rglob("*.pdf"))
    print(f"Found {len(pdf_files)} PDFs.")
    
    print("\nSample PDFs (Filesystem):")
    for p in pdf_files[:5]:
        print(f" - {p} (Stem: {p.stem})")
        
    print("\nMismatch Analysis:")
    # Replicate qa.py logic
    indexed_stems = set()
    for docname in docs.docnames:
        if " pages " in docname:
            paper_name = docname.split(" pages ")[0]
            stem = Path(paper_name).stem.lower()
            indexed_stems.add(stem)
        else:
            stem = Path(docname).stem.lower()
            indexed_stems.add(stem)
            
    print("\nDoc Metadata Inspection:")
    count = 0
    for docname in list(docs.docnames):
        try:
            # PaperQA2 might store docs in docs.docs or docs.texts? 
            # docs object has 'docs' attribute which is Dict[str, Doc]
            if hasattr(docs, 'docs') and docname in docs.docs:
                 doc = docs.docs[docname]
                 print(f"Doc: {docname}")
                 # Check for citation, dockey, or anything related to filename
                 if hasattr(doc, 'citation'): print(f"  Citation: {doc.citation}")
                 if hasattr(doc, 'dockey'): print(f"  Dockey: {doc.dockey}")
                 # Try to find source path
                 # Usually doc.dockey might contain path hash?
                 pass
            elif hasattr(docs, 'texts'):
                 # Inspect texts index?
                 pass
            count += 1
            if count >= 3: break
        except Exception as e:
            print(f"Error inspecting {docname}: {e}")

if __name__ == "__main__":
    debug_pickle()
