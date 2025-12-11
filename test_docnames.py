#!/usr/bin/env python3
"""Test if our fix works."""
import asyncio
from pathlib import Path
import sys

repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root / "scripts"))

from qa import load_existing_docs
from qdrant_client import AsyncQdrantClient

async def test():
    library_path = repo_root / "library"
    db_path = library_path / ".qa_vectordb"
    
    client = AsyncQdrantClient(path=str(db_path))
    
    try:
        docs = load_existing_docs(client)
        
        if docs:
            print(f"✓ Loaded docs object")
            print(f"  Docnames count: {len(docs.docnames) if docs.docnames else 0}")
            
            if docs.docnames:
                print(f"\n  Sample docnames:")
                for name in list(docs.docnames)[:5]:
                    print(f"    - {name}")
                
                # Extract paper names
                indexed_papers = set()
                for docname in docs.docnames:
                    if " pages " in docname:
                        paper_name = docname.split(" pages ")[0].lower()
                        indexed_papers.add(paper_name)
                    else:
                        indexed_papers.add(docname.lower())
                
                print(f"\n  Unique papers extracted: {len(indexed_papers)}")
                print(f"  Sample paper names:")
                for name in list(indexed_papers)[:10]:
                    print(f"    - {name}")
                
                # Check against actual PDFs
                pdf_files = list(library_path.rglob("*.pdf"))
                print(f"\n  Total PDFs in library: {len(pdf_files)}")
                
                matched = 0
                for pdf in pdf_files[:10]:
                    if pdf.stem.lower() in indexed_papers:
                        matched += 1
                        print(f"    ✓ {pdf.stem}")
                    else:
                        print(f"    ✗ {pdf.stem} NOT IN INDEX")
                
                print(f"\n  Matched {matched}/10 sample PDFs")
        else:
            print("✗ Failed to load docs")
            
    finally:
        await client.close()

asyncio.run(test())
