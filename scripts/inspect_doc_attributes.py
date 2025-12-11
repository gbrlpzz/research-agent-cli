
import pickle
from pathlib import Path
from paperqa import Docs

def inspect_attributes():
    library_path = Path("library")
    pkl_path = library_path / ".qa_docs.pkl"
    
    if not pkl_path.exists():
        print("Pickle missing")
        return

    with open(pkl_path, 'rb') as f:
        docs = pickle.load(f)
    
    print(f"Loaded {len(docs.docnames)} docs")
    if len(docs.docnames) > 0:
        first_key = list(docs.docnames)[0]
        # In PaperQA v2+, docs.docs might be the dictionary
        print(f"Docs attributes: {dir(docs)}")
        if hasattr(docs, 'docs'):
             print(f"Keys in docs.docs: {list(docs.docs.keys())[:5]}")
             # Access using a valid key
             for k in list(docs.docs.keys())[:1]:
                 doc = docs.docs[k]
                 print(f"\nAttributes of Doc '{k}':")
                 for ak, av in doc.__dict__.items():
                     print(f"  {ak}: {str(av)[:100]}") # Truncate values
        else:
             print("Attributes of Docs object:")
             print(dir(docs))

if __name__ == "__main__":
    inspect_attributes()
