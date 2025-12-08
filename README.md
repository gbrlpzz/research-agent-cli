# Research Agent CLI

> **Autonomous Research Assistant & Paper Writer**

The Research Agent CLI is a powerful tool that autonomously conducts academic research, manages your bibliography, and synthesizes findings into high-quality Typst documents. Powered by Google's Gemini 3 Pro and PaperQA2.

## 🚀 Features

- **Autonomous Research**: Give it a topic, and it plans, searches, reads, and synthesizes information.
- **Multi-Source Discovery**: Finds papers via Semantic Scholar, Exa.ai, and custom scrapers.
- **PDF Fetching**: Automatically downloads PDFs from ArXiv, Unpaywall (open access), and Sci-Hub (fallback).
- **Local Library**: Manages a local PDF library using `papis` and `master.bib`.
- **RAG-Powered**: Uses PaperQA2 + Qdrant for deep, evidence-based answers from your library.
- **Citation Management**: Automatically handles BibTeX citations and verifies them before writing.
- **Typst Output**: Generates beautiful, publication-ready PDFs using a custom Typst template.

## 🛠️ Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/research-agent-cli.git
   cd research-agent-cli
   ```

2. **Set up the environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**:
   Create a `.env` file in the root directory:
   ```env
   GEMINI_API_KEY=your_gemini_key
   EXA_API_KEY=your_exa_key (optional)
   SEMANTIC_SCHOLAR_API_KEY=your_key (optional)
   ```

4. **Dependencies**:
   - Ensure `typst` is installed and in your PATH.
   - Ensure `papis` is installed (included in requirements).

## 📖 Usage

### Run the Agent

To start a new autonomous research task:

```bash
research agent "The impact of attention mechanisms on NLP"
```

The agent will:
1. Search for relevant papers.
2. Download them to your library.
3. Read and synthesize the content.
4. Generate a report in `reports/<timestamp>_<topic>/`.

### Subcommands

The CLI provides several tools to manage your research:

| Command | Description |
|---------|-------------|
| `research <query>` | Discover papers using Unified Search (Semantic Scholar + Paper-Scraper) |
| `research agent <topic>` | Run the autonomous research agent |
| `research qa <question>` | Ask detailed questions about your library (RAG-powered) |
| `research add <id>` | Add a paper by DOI or arXiv ID |
| `research cite [query]` | Search library and copy citation keys (@key) to clipboard |
| `research open [query]` | Open a paper from your library in the browser |
| `research exa <query>` | Perform a semantic/neural search using Exa.ai (costs credits) |
| `research edison <query>` | Generate an AI literature synthesis (costs 1 credit) |

### Examples

```bash
# Add a paper by DOI
research add 10.1234/example

# Search for papers about vision transformers
research "vision transformers"

# Ask a question to your library
research qa "How does self-attention differ from LSTM?"

# Find a citation key for "Vaswani"
research cite vaswani
```

## 🏗️ Architecture

See [docs/architecture.md](docs/architecture.md) for a detailed breakdown of the system components.

## 📂 Directory Structure

- `scripts/`: Python source code.
- `library/`: Your local collection of papers (PDFs).
- `reports/`: Generated research outputs.
- `docs/`: Documentation.
- `templates/`: Typst templates for document generation.

## 📄 License

[MIT](LICENSE)
