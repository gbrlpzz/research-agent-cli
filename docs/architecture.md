# System Architecture

## Overview

The Research Agent CLI is an autonomous system designed to conduct academic research, manage a bibliography, and generate high-quality papers using Typst. It orchestrates a pipeline of discovery, acquisition, synthesis, and writing using advanced LLMs (Gemini).

## Core Components

### 1. Agent Orchestrator (`scripts/agent.py`)
The central brain of the system.
- **Model**: Uses `gemini-3-pro-preview` for high-level planning and writing.
- **Function Calling**: Defines a set of tools (`discover_papers`, `add_paper`, `query_library`, etc.) that the model can invoke to interact with the world.
- **Loop**: Follows a RAG-first iterative workflow: Query Library -> Identify Gaps -> Search -> Acquire -> Repeat -> Write.

### 2. Paper Discovery
Multi-source discovery engine to find relevant literature.
- **Semantic Scholar**: Primary source for academic metadata and citation graphs.
- **Paper-Scraper**: Custom scraper for specific repositories (arXiv, PubMed, etc.).
- **Exa.ai**: Neural search for conceptual queries and finding papers that keyword search might miss.

### 3. Library Management
Local management of physical PDF files and metadata.
- **Storage**: PDF files stored in `library/`.
- **Metadata**: `master.bib` maintains the BibTeX database.
- **Papis**: Underlying CLI tool used for adding and managing papers.
- **Synchronization**: `scripts/utils/sync_bib.py` ensures `master.bib` stays valid and safe (atomic writes, backups in `.backups/`).

### 4. RAG & Synthesis Engine
Retrieval-Augmented Generation system for deep understanding of papers.
- **PaperQA2**: Framework for digging into PDF content.
- **Qdrant**: Persistent vector database (stored in `library/.qa_vectordb`) for fast embedding retrieval.
- **Embeddings**: `gemini/text-embedding-004`.
- **LLM**: `gemini-2.5-flash` for fast summarization and answer extraction.

### 5. Reporting & Output
Generates publication-ready documents.
- **Typst**: Modern typesetting system used for the final output.
- **Templates**: `templates/typst-template` defines the look and feel.
- **Artifacts**: Reports are generated in `reports/<timestamp>_<topic>/`, containing:
  - `main.typ`: The source code.
  - `main.pdf`: The compiled document.
  - `refs.bib`: A filtered bibliography containing only cited papers.

## Data Flow

1. **User Input**: `research agent "Topic"`
2. **Planning**: Agent analyzes topic and queries existing library.
3. **Discovery**: Agent searches external APIs for new papers.
4. **Acquisition**: Papers are downloaded to `library/` and added to `master.bib`.
5. **Indexing**: New papers are embedded into Qdrant.
6. **Synthesis**: Agent queries the RAG engine to answer specific questions.
7. **Writing**: Agent generates Typst code with accurate `@citation_keys`.
8. **Compilation**: Typst compiles the final PDF.

## Directory Structure

```
research-agent-cli/
├── .backups/        # Backups of master.bib
├── library/         # PDF files and Qdrant DB
├── reports/         # Generated research reports
├── scripts/         # Python tools and agent code
│   ├── agent.py     # Main entry point
│   └── utils/       # Helpers (sync_bib, etc.)
├── templates/       # Typst templates
└── master.bib       # Global bibliography
```
