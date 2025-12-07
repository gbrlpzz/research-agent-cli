# Research Agent CLI

A terminal-first research pipeline integrating **Semantic Scholar**, **Papis**, and **Typst**.

## Features

- **Smart Discovery**: Search Semantic Scholar via CLI (`research "query"`).
- **Interactive Selection**: Use `fzf` to multi-select papers from results.
- **Auto-Archiving**: Automatically downloads PDFs and bibtex to `library/`.
- **Writing Scaffold**: Automatically creates a `main.typ` and `refs.bib` for every paper you add.

## Setup

### Prerequisites

- `python3`
- `fzf`
- `typst`
- `git`

### Installation

1.  Clone this repository:
    ```bash
    git clone <your-repo-url> ~/Documents/Github/research
    cd ~/Documents/Github/research
    ```

2.  Install Python dependencies:
    ```bash
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    ```

3.  Link the executable to your path:
    ```bash
    mkdir -p ~/.local/bin
    ln -s $(pwd)/bin/research ~/.local/bin/research
    ```
    *Ensure `~/.local/bin` is in your `$PATH`.*

## Usage

### 1. Discovery
Run the `research` command from anywhere:

```bash
research "large language models reasoning"
```

1.  A list of papers appears (Title, Year, Citations).
2.  **Tab** to select multiple papers.
3.  **Enter** to download them.

### 2. Management
Your papers are stored in `./library`. Use `papis` to manage them (already installed in the venv, or use system papis):

```bash
# List all papers
papis list

# Open a paper
papis open
```

### 3. Writing
Every paper added gets a `main.typ` automatically created in its folder.

```bash
cd library/2023-Smith-reasoning-in-llms
typst watch main.typ
```

The `main.typ` is pre-configured to link to your central `master.bib` (managed by Papis).

## Directory Structure

```
research/
├── library/            # Where papers live (one folder per paper)
│   └── 2023-Author-Title/
│       ├── info.yaml   # Metadata
│       ├── paper.pdf   # The document
│       └── main.typ    # Your notes/draft
├── scripts/            # Python logic
├── bin/                # Executable entry points
└── papis.config        # Papis configuration
```
