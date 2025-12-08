# Research Agent CLI

A terminal-based research pipeline for discovery, acquisition, management, and synthesis of academic literature.

## Architecture Overview

The system operates as a unified pipeline:

1.  **Discovery**: Aggregates results from Semantic Scholar, Exa.ai (neural search), and Paper-Scraper (publisher direct).
2.  **Acquisition**: Automatically resolves and downloads PDFs via ArXiv, Unpaywall, or direct scraping.
3.  **Management**: Organizes metadata and files using `papis`, exposing a local API for citation and retrieval.
4.  **Synthesis (RAG)**: Indexes local PDFs into a Qdrant vector database and uses Google Gemini (2.5 Flash) for retrieval-augmented generation.

## Tool Reference & Technologies

This project coordinates the following tools and libraries:

| Component | Tool / Library | Purpose | Source |
| :--- | :--- | :--- | :--- |
| **Core Logic** | **Python 3.11+** | Runtime environment. | [python.org](https://www.python.org/) |
| | **Papis** | Underlying bibliography and document manager. | [papis/papis](https://github.com/papis/papis) |
| | **FZF** | Command-line fuzzy finder for interactive selection. | [junegunn/fzf](https://github.com/junegunn/fzf) |
| **Search** | **Semantic Scholar API** | Primary academic graph search (~200M papers). | [semanticscholar/semanticscholar](https://github.com/allenai/semanticscholar) |
| | **Exa.ai SDK** | Neural/semantic search for concept-based queries. | [exa-labs/exa-py](https://github.com/exa-labs/exa-py) |
| | **Paper-Scraper** | Scraper for PubMed, bioRxiv, Springer, etc. | [blackadad/paper-scraper](https://github.com/blackadad/paper-scraper) |
| **RAG & AI** | **PaperQA** | ORM for RAG; handles indexing, citation, and prompting. | [Future-House/paper-qa](https://github.com/Future-House/paper-qa) |
| | **Qdrant** | Local vector database for persistent embeddings. | [qdrant/qdrant-client](https://github.com/qdrant/qdrant-client) |
| | **Google Gemini** | LLM (2.5 Flash) and Embeddings (`text-embedding-004`). | [google/generative-ai-python](https://github.com/google/generative-ai-python) |

## Installation

### Prerequisites
-   **Python 3.11+**
-   `fzf` installed on your system path.
-   `git`

### Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/gbrlpzz/research-agent-cli ~/Documents/GitHub/research-agent-cli
    cd ~/Documents/GitHub/research-agent-cli
    ```

2.  **Initialize environment:**
    ```bash
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configure Environment Variables:**
    Create a `.env` file in the root directory:
    ```bash
    # Required for RAG/QA
    GEMINI_API_KEY=your_gemini_key

    # Optional for Semantic Search
    EXA_API_KEY=your_exa_key
    ```
    *Note: Gemini API is currently free.*

4.  **System Integration (Optional):**
    Link the binary to your path and configure Papis:
    ```bash
    mkdir -p ~/.local/bin
    ln -s $(pwd)/bin/research ~/.local/bin/research
    
    mkdir -p ~/Library/Application\ Support/papis
    cp papis.config ~/Library/Application\ Support/papis/config
    ```

## Usage

### Discovery
Unified search across all configured providers.

```bash
# Standard search (Semantic Scholar + Paper-Scraper)
research "large language models reasoning"

# Neural/Semantic search (Exa.ai) - requires credit
research exa "papers about chain of thought reasoning"
```

### Management
Add papers via identifier or manage citations.

```bash
# Add by DOI or ArXiv ID (auto-fetches PDF)
research add 10.1038/nature12373
research add 1706.03762

# Interactive citation search (copies @key to clipboard)
research cite "attention"
```

### Knowledge Retrieval (Q&A)
Query your local library using Retrieval Augmented Generation (RAG).

**Features:**
- **Zero-Latency**: Uses a local **Qdrant** vector database (stored in `library/.qa_vectordb`) for instant queries.
- **Smart Indexing**: Automatically re-indexes only when you add or modify PDFs.
- **Gemini 2.5**: Powered by Google's latest Gemini 2.5 Flash model for high-speed, accurate reasoning.

```bash
# Single shot question (instant results after first run)
research qa "How does the attention mechanism differ from RNNs?"

# Interactive chat session (persists context)
research qa --chat

# Filter context to specific papers (works with instant search)
research qa --papers "vaswani" "Explain the transformer architecture"
```

## Directory Structure

```text
research-agent-cli/
├── bin/                    # Executable entry points
├── library/                # Document storage (gitignored)
│   ├── .qa_vectordb/       # Qdrant persistent vector store
│   └── <paper_id>/         # Papis document folders (info.yaml + PDF)
├── scripts/                # Python implementation
│   ├── discover.py         # Search & Deduplication logic
│   ├── qa.py               # RAG implementation (PaperQA + Gemini)
│   └── utils/              # PDF fetching and helpers
├── master.bib              # Auto-generated BibTeX for external usage
├── papis.config            # Papis configuration file
└── requirements.txt        # Dependency definitions
```

## License
Apache 2.0

## Changelog

### v0.2.0 - Gemini & Persistence Update
- **Feature**: Replaced in-memory indexing with **Qdrant** persistent vector storage (`library/.qa_vectordb`).
- **Performance**: Instant query responses on subsequent runs.
- **Model**: Upgraded default LLM to **Gemini 2.5 Flash** (Dec 2025).
- **UX**: Completely silenced verbose logs; added clean progress spinners.
- **Setup**: Removed OpenAI dependency; now fully usable with just `GEMINI_API_KEY`.

