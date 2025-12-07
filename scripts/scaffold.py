import sys
import os
from pathlib import Path

def create_typst_project(doc_path):
    """
    Scaffolds a Typst project in the paper's directory.
    """
    path = Path(doc_path)
    if not path.exists():
        print(f"Error: Path {path} does not exist.")
        return

    # 1. Create main.typ
    main_typ_content = '''
#set page(paper: "a4", margin: 2cm)
#set text(font: "Linux Libertine", lang: "en")

// Link to the master bibliography (adjust path relative to your structure)
#let master-bib = bibliography("../../master.bib")

= Notes on current paper

== Abstract
// Todo: Paste abstract here or pull from metadata

== Key Insights
- 

#bibliography("../../master.bib")
'''
    
    with open(path / "main.typ", "w") as f:
        f.write(main_typ_content)
    
    # 2. Create an empty local refs.bib if they want to override/isolate later
    with open(path / "refs.bib", "w") as f:
        f.write("")

    print(f"Scaffolded Typst project in {path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scaffold.py <doc_path>")
        sys.exit(1)
    
    # Papis passes the folder path as the argument
    create_typst_project(sys.argv[1])
