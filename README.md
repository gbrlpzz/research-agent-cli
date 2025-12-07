# Research Agent CLI

A terminal-first research pipeline integrating **Semantic Scholar**, **Papis**, and **Typst**.

## Features

- **Smart Discovery**: Search Semantic Scholar via CLI (`research "query"`).
- **Interactive Selection**: Use `fzf` to multi-select papers from results.
- **Auto-Archiving**: Automatically downloads PDFs and metadata to `library/`.
- **Central Bibliography**: All citations exported to `master.bib`.
- **Citation Search**: Quickly find and copy citation keys (`research cite`).
- **Open in Browser**: Press `o` while browsing to open papers directly.

## Setup

### Prerequisites

- `python3`
- `fzf`
- `typst` (optional, for writing)
- `git`

### Installation

1.  Clone this repository:
    ```bash
    git clone https://github.com/yourusername/research ~/Documents/GitHub/research
    cd ~/Documents/GitHub/research
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

4.  Copy papis config to system location:
    ```bash
    mkdir -p ~/Library/Application\ Support/papis
    cp papis.config ~/Library/Application\ Support/papis/config
    ```

## Usage

### Commands

| Command | Description |
|---------|-------------|
| `research <query>` | Search Semantic Scholar, add papers |
| `research add [id]` | Quick add from DOI/arXiv (or clipboard) |
| `research cite [query]` | Search library, copy citation keys |
| `research open [query]` | Search library, open paper in browser |

### 1. Discovery - Add Papers

```bash
research "large language models reasoning"
```

1. A list of papers appears (Year, Citations, Title, Authors).
2. **Tab** to select multiple papers.
3. **o** to open highlighted paper in browser.
4. **Enter** to download selected papers.

### 2. Quick Add

```bash
# Add by DOI
research add 10.1038/nature12373

# Add by arXiv ID
research add 1706.03762

# Add from clipboard (just copy a DOI and run)
research add
```

### 3. Citation Search

```bash
# Browse all citations
research cite

# Pre-filter by author, title, or year
research cite egger
research cite 2023
research cite "meta-analysis"
```

1. Search/filter interactively with fzf.
2. **o** to open paper in browser.
3. **Tab** to multi-select.
4. **Enter** to copy `@citation_key` to clipboard.

### 4. Open Papers

```bash
research open                # Browse all
research open attention      # Filter and open
```

### 5. Use in Typst

In any Typst document, reference the central bibliography:

```typst
// In /docs/my-paper/main.typ
#set document(title: "My Paper")

This is discussed in @Attention_Is_Al_Vaswan_2017.

#bibliography("../../research/master.bib")
```

**Workflow:**
1. `research cite attention` → copies `@Attention_Is_Al_Vaswan_2017`
2. Paste into your `.typ` file
3. Typst compiles with the reference

## Directory Structure

```
research/
├── library/            # Papers (private, gitignored)
│   └── <papis-id>/
│       ├── info.yaml   # Metadata
│       └── paper.pdf   # Document
├── master.bib          # Bibliography (private, gitignored)
├── scripts/            # Python logic
│   ├── discover.py     # Paper search & download
│   ├── add.py          # Quick add from DOI/arXiv
│   ├── cite.py         # Citation search
│   └── open.py         # Open in browser
├── bin/                # Executable
│   └── research        # CLI wrapper
├── papis.config        # Papis configuration
└── requirements.txt    # Python dependencies
```

## License

MIT
