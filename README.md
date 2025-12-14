<p align="center">
  <h1 align="center">Research Agent CLI</h1>
  <p align="center">
    <strong>Autonomous Academic Research Pipeline</strong>
  </p>
  <p align="center">
    A tool-calling agent that discovers papers, builds a local library, and produces cited Typst documents through iterative peer review.
  </p>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#cli-reference">CLI Reference</a> •
  <a href="#configuration">Configuration</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="License">
  <img src="https://img.shields.io/badge/LiteLLM-Multi--Model-orange.svg" alt="LiteLLM">
  <img src="https://img.shields.io/badge/PaperQA2-RAG-purple.svg" alt="PaperQA2">
</p>

---

## What This Does

Given a research topic, the agent:

1. **Decomposes** the topic into research questions and an argument map
2. **Searches** Semantic Scholar, Exa.ai, and citation networks for relevant papers
3. **Downloads** PDFs via ArXiv, Unpaywall, or configured private sources
4. **Indexes** papers into a persistent Qdrant vector database
5. **Queries** the indexed library using PaperQA2 RAG to ground claims
6. **Drafts** a structured Typst document with inline citations
7. **Reviews** the draft via an automated peer reviewer that checks citation validity
8. **Revises** until the reviewer accepts or max rounds are reached

The output is a compiled PDF with a filtered bibliography containing only cited works.

---

## Quick Start

```bash
# Clone
git clone https://github.com/gbrlpzz/research-agent-cli.git
cd research-agent-cli

# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure (requires at least one LLM API key)
cp .env.example .env
# Edit .env: set GEMINI_API_KEY or OPENAI_API_KEY

# Install Typst for PDF compilation
brew install typst  # macOS
# Or: https://github.com/typst/typst/releases

# Run
research agent "Attention mechanisms in transformer architectures"
```

Output is written to `reports/<timestamp>_<topic>/`:
```
├── main.typ           # Typst source
├── main.pdf           # Compiled document  
├── refs.bib           # Bibliography (cited papers only)
├── literature_sheet.csv
└── artifacts/         # Plans, drafts, reviews
```

### Interactive Mode

```bash
research agent -i "Your topic"
```

Prompts for model selection (Gemini 3 Pro / 2.5 Flash / 2.5 Pro), max iterations, and revision rounds before starting.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              RESEARCH AGENT                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   │
│  │ Planning │ → │ Discovery│ → │ Indexing │ → │ Drafting │ → │  Review  │   │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └────┬─────┘   │
│       │              │              │              │              │         │
│  Research       Semantic       PaperQA2 +      Tool-calling    Verify       │
│  questions      Scholar,       Qdrant          agent loop      citations    │
│  + argument     Exa.ai,        persistent                      + claims     │
│    map          citation       vector index                                 │
│                 networks                            ▲              │        │
│                                                     │              ▼        │
│                                              ┌──────┴───────────────────┐   │
│                                              │  Revision (if needed)    │   │
│                                              └──────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │  Typst + PDF │
                               └──────────────┘
```

### Phase Details

| Phase | What Happens |
|-------|--------------|
| **Planning** | LLM generates research questions, key concepts, search queries, and an argument map with evidence requirements |
| **Discovery** | Searches Semantic Scholar API; optionally Exa.ai (neural search) and citation network traversal |
| **Acquisition** | Downloads PDFs from ArXiv, Unpaywall, or private sources; stores in Papis-managed library |
| **Indexing** | PaperQA2 chunks PDFs and stores embeddings in a persistent Qdrant database |
| **Drafting** | Agent loop with tools: `query_library`, `discover_papers`, `fuzzy_cite`, `validate_citations` |
| **Review** | Separate reviewer agent checks citation validity, claim grounding, and coverage |
| **Revision** | If reviewer requests changes, revision agent incorporates feedback |

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `research agent <topic>` | Run full research pipeline |
| `research agent -i <topic>` | Interactive mode (select model, iterations) |
| `research agent -r N <topic>` | Set max revision rounds (default: 3) |
| `research agent --resume <path>` | Resume interrupted research from checkpoint |
| `research qa <question>` | Query your library with RAG |
| `research qa -i` | Interactive QA chat |
| `research <query>` | Search for papers (Semantic Scholar + paper-scraper) |
| `research add <id>` | Add paper by DOI or arXiv ID |
| `research cite [query]` | Fuzzy search citation keys |
| `research open [query]` | Open paper in browser |
| `research exa <query>` | Neural search via Exa.ai |

### Agent Flags

```bash
research agent [OPTIONS] <topic>

Options:
  -i, --interactive          Config menu before starting
  --resume PATH              Resume from existing report directory
  -r, --revisions N          Max peer review rounds (default: 3)
  --reviewers N              Parallel reviewers (default: 1)
  --reasoning-model MODEL    LLM for planning/writing/reviewing
  --rag-model MODEL          LLM for PaperQA RAG queries
  --embedding-model MODEL    Embedding model for indexing
```

### Resume Examples

```bash
# Resume from interrupted session
research agent --resume reports/20251212_150513_topic_name

# Resume with different model
research agent --resume reports/20251212_150513_topic_name \
  --reasoning-model gemini/gemini-3-pro-preview

# Resume and continue for more revision rounds
research agent --resume reports/20251212_150513_topic_name -r 5
```

---

## Configuration

### Environment Variables

Create `.env` in the project root:

```env
# At least one LLM API key is required
GEMINI_API_KEY=your_key          # For Gemini models
OPENAI_API_KEY=your_key          # For OpenAI models

# Model routing (optional, defaults shown)
RESEARCH_REASONING_MODEL=gemini/gemini-2.5-flash
RESEARCH_RAG_MODEL=gemini/gemini-2.5-flash
RESEARCH_EMBEDDING_MODEL=openai/text-embedding-3-large

# Discovery APIs (optional, improves paper finding)
SEMANTIC_SCHOLAR_API_KEY=your_key  # Higher rate limits
EXA_API_KEY=your_key               # Neural search

# Agent tuning (optional)
AGENT_MAX_ITERATIONS=50
REVISION_MAX_ITERATIONS=25
MAX_REVIEWER_ITERATIONS=15
API_TIMEOUT_SECONDS=120
```

### Model Options

| Model | Cost | Best For |
|-------|------|----------|
| `gemini/gemini-3-pro-preview` | ~$2.26/run | Complex reasoning, counter-arguments |
| `gemini/gemini-2.5-pro-preview` | ~$1.74/run | Balanced quality/cost |
| `gemini/gemini-2.5-flash` | ~$0.13/run | Fast iteration, simple topics |

Use `-i` flag to select interactively, or set via `--reasoning-model`.

---

## Project Structure

```
research-agent-cli/
├── bin/research              # CLI entry point
├── scripts/
│   ├── agent.py              # Main orchestrator
│   ├── phases/               # Modular pipeline phases
│   │   ├── planner.py        # Research planning
│   │   ├── drafter.py        # Document drafting
│   │   ├── reviewer.py       # Peer review
│   │   ├── reviser.py        # Revision handling
│   │   └── tool_registry.py  # Tool declarations
│   ├── tools/                # Shared tool implementations
│   ├── qa.py                 # RAG question-answering
│   └── utils/                # Helpers (LLM, prompts, typst)
├── library/                  # Local PDF collection
├── reports/                  # Generated outputs
├── templates/typst-template/ # Document template
└── master.bib                # Master bibliography
```

---

## Dependencies

| Component | Purpose |
|-----------|---------|
| [PaperQA2](https://github.com/Future-House/paper-qa) | RAG over academic papers |
| [Qdrant](https://qdrant.tech/) | Persistent vector storage |
| [LiteLLM](https://github.com/BerriAI/litellm) | Multi-provider LLM routing |
| [Papis](https://github.com/papis/papis) | Bibliography management |
| [Typst](https://typst.app/) | Document compilation |
| [Rich](https://github.com/Textualize/rich) | Terminal interface |
| [Semantic Scholar API](https://www.semanticscholar.org/product/api) | Paper discovery |

---

## Limitations

- **API Dependent**: Requires LLM API access (Gemini or OpenAI)
- **PDF Availability**: Some papers may not have accessible PDFs
- **Citation Accuracy**: Generated citations should be manually verified
- **Rate Limits**: Semantic Scholar and Exa.ai have request limits
- **English Only**: Optimized for English-language research

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for details.

---

## License

Apache License 2.0. See [LICENSE](LICENSE).
