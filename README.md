# Research Agent CLI

**A complete terminal-first research pipeline** — from discovery to understanding.

Combines multi-source paper search, automatic PDF fetching, citation management, and AI-powered question answering into a unified CLI workflow.

## What It Does

### 🔍 **Discovery & Collection**
- **Unified Multi-Source Search**: Single command queries Semantic Scholar + paper-scraper (PubMed, arXiv, bioRxiv, Springer)
- **Smart Deduplication**: Automatically merges duplicates by DOI/arXiv ID
- **Source Transparency**: Results tagged with origin ([S2], [PS])
- **Alternative Backends**: Semantic search via Exa.ai, AI synthesis via Edison Scientific
- **Automatic PDF Fetching**: Downloads from ArXiv, Unpaywall, and paper-scraper
- **Interactive Selection**: Multi-select papers with `fzf`, open in browser with `o`

### 📚 **Library Management**
- **Auto-Archiving**: PDFs and metadata saved to local `library/` directory
- **Central Bibliography**: All citations auto-exported to `master.bib`
- **Citation Search**: Quickly find and copy BibTeX keys for Typst/LaTeX
- **Papis Integration**: Powerful metadata management and organization

### 🤖 **AI-Powered Understanding**
- **Question Answering**: Ask questions about your local library using Gemini
- **Cited Responses**: Answers include source papers and page numbers
- **Interactive Chat**: Follow-up questions in terminal (coming soon)
- **Export Capability**: Save Q&A sessions to dedicated directory

### ⚡ **Key Features**
- Progress indicators with spinners and checkmarks
- Graceful degradation (works even if sources fail)
- Free tier available for all features
- Works offline for cached queries and local library

## Setup

### Prerequisites

- **Python 3.11+** (required for Edison Scientific client)
- `fzf` (for interactive paper selection)
- `typst` (optional, for writing)
- `git`

### Installation

1.  Clone this repository:
    ```bash
    git clone https://github.com/gbrlpzz/research-agent-cli ~/Documents/GitHub/research-agent-cli
    cd ~/Documents/GitHub/research-agent-cli
    ```

2.  Install Python dependencies:
    ```bash
    # Use Python 3.11 or higher
    python3.11 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    ```

3.  **(Optional) Set up Exa.ai for semantic search:**
    - Get your API key from [Exa.ai Dashboard](https://dashboard.exa.ai)
    - Create a `.env` file in the repo root:
      ```bash
      echo "EXA_API_KEY=your_key_here" > .env
      ```
    - **Cost**: Free tier provides 1,000 credits

4.  **(Optional) Set up Edison Scientific for AI literature synthesis:**
    - Get your API key from [Edison Platform](https://platform.edisonscientific.com/profile)
    - Add to `.env` file:
      ```bash
      echo "EDISON_API_KEY=your_key_here" >> .env
      ```
    - **Cost**: Free tier provides 10 credits/month (students get 210 credit bonus)

5.  **(Optional) Set up Gemini for question-answering:**
    - Get your free API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
    - Add to `.env` file:
    -   ```bash
        echo "GEMINI_API_KEY=your_key_here" >> .env
        ```
    - **Cost**: **Completely Free** (Uses Gemini 2.5 Flash + Gemini Embeddings)
    - **Usage**: Powers `research qa` to answer questions with citations from your local library

6.  Link the executable to your path:
    ```bash
    mkdir -p ~/.local/bin
    ln -s $(pwd)/bin/research ~/.local/bin/research
    ```
    *Ensure `~/.local/bin` is in your `$PATH`.*

7.  Copy papis config to system location:
    ```bash
    mkdir -p ~/Library/Application\ Support/papis
    cp papis.config ~/Library/Application\ Support/papis/config
    ```

## Usage

### Commands

| Command | Description | Sources | Cost |
|---------|-------------|---------|------|
| `research <query>` | Unified multi-source search | S2 + PS | Free |
| `research exa <query>` | Semantic/neural search | Exa.ai | 1 credit |
| `research edison <query>` | AI literature synthesis | Edison | 1 credit |
| `research edison list` | Browse past Edison reports | - | Free |
| `research edison show <id>` | View specific report | - | Free |
| `research edison cache <query>` | Check if query cached | - | Free |
| `research edison credits` | Show credit balance | - | Free |
| `research add [id]` | Quick add from DOI/arXiv | - | Free |
| `research cite [query]` | Search library, copy citation keys | - | Free |
| `research open [query>` | Open paper in browser | - | Free |
| `research qa <question>` | Ask questions about your library | Gemini | Free* |
| `research qa --papers <pattern> <question>` | Query specific papers only | Gemini | Free* |
| `research qa --export <dir> <question>` | Save answer to markdown file | Gemini | Free* |
| `research qa --chat` | Interactive Q&A session | Gemini | Free* |
| `research help` | Interactive tutorial with examples | - | Free |

**Source Codes**: S2 = Semantic Scholar, PS = paper-scraper (PubMed, arXiv, bioRxiv, Springer)  
**Free***: Requires free Gemini API key (generous quota)

### 1. Discovery - Unified Multi-Source Search

**Default search (searches ALL free sources simultaneously):**
```bash
research "large language models reasoning"
```

**What happens**:
1. 🔄 Searches Semantic Scholar (~200M academic papers)
2. 🔄 Searches paper-scraper (PubMed, arXiv, bioRxiv, Springer)
3. ✓ Merges and deduplicates results by DOI/arXiv ID
4. 📊 Shows unified results with source tags: `[S2]` or `[PS]`

**Example output**:
```
⠋ Searching Semantic Scholar...
✓ Semantic Scholar: 15 results
⠋ Searching paper-scraper...
✓ Paper-scraper: 8 results
✓ Found 21 unique papers (15 from S2, 8 from PS)

[S2] 2017 |  8642 cites | Attention Is All You Need - Vaswani et al.
[PS] 2023 |       --    | Transformers in Vision - Smith et al.
```

**Alternative search modes:**

**Exa.ai (neural/semantic search, costs 1 credit):**
```bash
research exa "papers about reasoning similar to chain-of-thought"
```

**Edison Scientific (AI synthesis, costs 1 credit):**
```bash
research edison "What are the latest advances in few-shot learning?"
```

**Interactive selection**:  
1. Results appear in fzf with source tags and metadata
2. **Tab** to select multiple papers
3. **o** to open highlighted paper in browser
4. **Enter** to download selected papers

**PDF Fetching**: Automatic downloads from:
- **ArXiv**: Direct PDF download
- **Unpaywall**: Free, legal open-access PDFs for DOIs
- **Paper-scraper**: Downloads when available

### 2. Quick Add

```bash
# Add by DOI (with PDF if available)
research add 10.1038/nature12373

# Add by arXiv ID (with PDF)
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

### 6. Knowledge Retrieval (Q&A)

Ask complex questions about your entire PDF library. The agent reads papers, extracts evidence, and synthesizes an answer with citations.

```bash
research qa "How does the attention mechanism differ from RNNs?"
```

**What happens**:
1. ⠋ Indexes your new PDFs (using free Gemini embeddings)
2. ⠋ Selects relevant passages using vector search
3. 🤖 Synthesizes an answer using **Gemini 2.5 Flash**

**Example Output**:
```
Answer:
The attention mechanism allows modeling of dependencies without regard to their distance in the input or output sequences [1]. Unlike RNNs, which process data sequentially and preclude parallelization, the Transformer architecture relies entirely on attention to draw global dependencies between input and output [1][2]. This allows for significantly more parallelization and faster training times [1].

Sources:
[1] Attention Is All You Need - Vaswani et al. (Relevance: 10.00)
[2] Transformers in Vision - Smith et al. (Relevance: 8.50)
```

## Directory Structure

```
research-agent-cli/
├── library/                    # Papers (private, gitignored)
│   ├── <papis-id>/
│   │   ├── info.yaml          # Metadata
│   │   └── paper.pdf          # Document (auto-fetched)
│   └── edison_reports/        # Edison literature reports
│       ├── <timestamp>_<query>.md
│       ├── tables/            # Extracted CSV tables
│       └── reports_index.json
├── master.bib                 # Central bibliography (gitignored)
├── scripts/                   # Python logic
│   ├── discover.py           # Unified multi-source search (S2 + PS)
│   ├── exa_search.py         # Exa.ai semantic search
│   ├── edison_literature.py  # Edison AI synthesis
│   ├── add.py                # Quick add from DOI/arXiv
│   ├── cite.py               # Citation search
│   ├── open.py               # Open in browser
│   └── utils/
│       ├── pdf_fetcher.py    # PDF download utility
│       └── scraper_client.py # Paper-scraper integration
├── bin/
│   └── research              # CLI wrapper
├── papis.config              # Papis configuration
└── requirements.txt          # Python dependencies
```

## Implementation Notes

### Automatic PDF Fetching

All search modes (Semantic Scholar, Exa.ai, Edison) now automatically fetch PDFs when adding papers:

**Sources** (in priority order):
1. **ArXiv**: Direct PDF download from `arxiv.org/pdf/<id>.pdf`
2. **Unpaywall API**: Free, legal open-access PDFs for DOIs
3. Falls back gracefully if no PDF available

**How it works**:
- When you select papers to add, PDFs are fetched before calling papis
- Download progress shown in terminal
- PDFs automatically attached to papis entries
- Temporary files cleaned up after import

**Example output**:
```
Attempting PDF download from ArXiv...
✓ PDF downloaded from ArXiv
Adding arxiv:2301.00001...
```

### Edison Scientific Integration

Edison provides AI-powered literature synthesis with comprehensive reports:

**Features**:
- AI-synthesized answers with citations
- Automatic table extraction (markdown + CSV)
- Citation parsing with DOI/ArXiv extraction
- Report storage in `library/edison_reports/`
- Integration with papis/master.bib workflow

**API Details**:
- Uses `edison-client` package
- **Cost**: 1 credit per query
- Free tier: 10 credits/month (students get 210 credit signup bonus)
- Reports include synthesis, citations, tables, and metadata

**Example Report Structure**:
```markdown
# Literature Report: few-shot learning

**Generated**: 2024-12-08 14:00:00
**Credits Used**: 1

## Synthesis
[AI-generated synthesis...]

## Cited Papers
- [1] Author et al. (Year). Title.
  - DOI: 10.1234/example
  - ArXiv: 2301.00001

## Tables
Table 1: `tables/20241208_140000_table1.csv`
| Method | Accuracy | Year |
|--------|----------|------|
...
```

### Exa.ai Integration

Exa.ai provides semantic/neural search to complement Semantic Scholar's keyword-based search:

- **API**: Uses `exa_py` SDK with `search_and_contents()` method
- **Filtering**: Restricts results to `category="research paper"` and academic domains
- **Metadata Extraction**: Automatically extracts DOI/ArXiv IDs from URLs
- **Cost**: 1 credit per query (free tier: 1,000 credits)

### Python Version Requirement

**Important**: Edison Scientific client requires **Python 3.11 or higher**. Make sure to use:
```bash
python3.11 -m venv .venv
# or python3.12, python3.13, etc.
```

If you don't have Python 3.11+, install via homebrew:
```bash
brew install python@3.11
```

## Changelog

### 2025-12-08
- **QA Overhaul**: Refactored `research qa` for a cleaner, unified UX.
  - Replaced verbose debug logs with sleek spinners.
  - Switched to **Gemini Embeddings** (`text-embedding-004`), making the QA feature **100% free** (no OpenAI key required).
  - Upgraded to **Gemini 2.5 Flash** (brand new Dec 2025!) for faster, smarter answers.
- **Fixed**: Corrected papis CLI flag from `--file` to `--file-name` in `discover.py`.
- **Improved**: PDF fetching now properly attaches downloaded PDFs to papis entries.

## License

Apache 2.0 See License
Copyright (C) 2025 Gabriele Pizzi
