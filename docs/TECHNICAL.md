# Technical Reference

This document provides detailed technical documentation for the Research Agent CLI, covering architecture, implementation details, and system design decisions.

## Table of Contents

- [System Architecture](#system-architecture)
- [RAG Pipeline](#rag-pipeline)
- [Agent Workflow](#agent-workflow)
- [Tool Specifications](#tool-specifications)
- [Output Generation](#output-generation)
- [Configuration Reference](#configuration-reference)

---

## System Architecture

### High-Level Overview

The Research Agent CLI is built as a multi-phase agentic system that orchestrates:

1. **Planning Layer** - LLM-based research planning and argument mapping
2. **Discovery Layer** - Multi-source academic paper search
3. **Acquisition Layer** - PDF downloading and library management
4. **RAG Layer** - Question-answering over indexed documents
5. **Generation Layer** - Document writing with citation management
6. **Review Layer** - Automated peer review and revision

```
┌─────────────────────────────────────────────────────────────────┐
│                        Research Agent                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │  Planning   │  │  Discovery  │  │      Acquisition        │  │
│  │   (Gemini)  │──│ (S2/Exa/PS) │──│ (ArXiv/Unpaywall/Priv)  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│         │                                      │                 │
│         ▼                                      ▼                 │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    Papis Library (PDFs)                      │ │
│  └─────────────────────────────────────────────────────────────┘ │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              RAG Layer (PaperQA2 + Qdrant)                   │ │
│  │  ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐  │ │
│  │  │ text-emb-004  │  │ Qdrant VecDB  │  │  PaperQA2 Query │  │ │
│  │  └───────────────┘  └───────────────┘  └─────────────────┘  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                  Generation Layer                            │ │
│  │  ┌─────────┐  ┌─────────────┐  ┌──────────┐  ┌───────────┐  │ │
│  │  │ Drafting│──│ Self-Critique│──│ Review  │──│ Revision  │  │ │
│  │  └─────────┘  └─────────────┘  └──────────┘  └───────────┘  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Output (Typst → PDF + Bibliography)             │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Core Components

#### 1. LLM Integration (Gemini)

The agent uses two Gemini model configurations:

| Model | Purpose | Configuration |
|-------|---------|---------------|
| `gemini-2.5-flash` | Agent reasoning, writing, reviewing | Default for main agent |
| `gemini-2.5-flash` | RAG queries (via PaperQA2) | Faster model for QA |

Function calling is used extensively for tool invocation:

```python
TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="discover_papers",
            description="Search for papers...",
            parameters=types.Schema(...)
        ),
        # ... other tools
    ])
]
```

#### 2. Library Management (Papis)

Papers are stored in a Papis library structure:

```
library/
├── paper_key_1/
│   ├── info.yaml       # Metadata
│   └── document.pdf    # Full text
├── paper_key_2/
│   ├── info.yaml
│   └── document.pdf
└── ...
```

Bibliography is synchronized to `master.bib` via the `sync_bib.py` utility.

#### 3. Vector Database (Qdrant)

Embeddings are persisted in a local Qdrant instance:

```
library/.qdrant/
├── collection/
│   ├── segments/
│   └── ...
└── ...
```

Key parameters:
- **Collection name**: `research_papers`
- **Vector size**: 768 (text-embedding-004)
- **Distance metric**: Cosine similarity

---

## RAG Pipeline

### Overview

The RAG (Retrieval-Augmented Generation) pipeline uses PaperQA2 to answer questions with citations from indexed papers.

### Indexing Process

1. **PDF Discovery**: Scan `library/` for PDF files
2. **Manifest Check**: Compare against `library/.manifest.json` to identify new/changed files
3. **Chunking**: PaperQA2 splits documents into semantic chunks
4. **Embedding**: Each chunk is embedded via `text-embedding-004`
5. **Storage**: Vectors stored in Qdrant with metadata

```python
# Indexing flow (simplified)
manifest = load_manifest(library_path)
for pdf in library_path.rglob("*.pdf"):
    file_hash = compute_md5(pdf)
    if pdf.name not in manifest or manifest[pdf.name] != file_hash:
        await docs.aadd(pdf, settings=settings)
        manifest[pdf.name] = file_hash
save_manifest(library_path, manifest)
```

### Query Process

1. **Question Embedding**: Query is embedded using the same model
2. **Retrieval**: Top-k similar chunks retrieved from Qdrant
3. **Context Assembly**: Relevant chunks assembled with source citations
4. **Generation**: Gemini generates answer using retrieved context
5. **Citation Tracking**: Used papers tracked for bibliography generation

```python
async def query_library(question: str, paper_filter: Optional[str] = None):
    docs = await load_or_create_docs(library_path)
    response = await docs.aquery(question, settings=settings)
    return {
        "answer": response.answer,
        "sources": [s.citation for s in response.contexts]
    }
```

### Persistence Mechanism

The system uses a dual persistence strategy:

1. **Qdrant Database**: Vector embeddings persist across sessions
2. **Manifest File**: Tracks which files have been indexed to avoid reprocessing
3. **Pickle Cache**: Serialized `Docs` object for fast reload

```python
def get_manifest_path(library_path):
    return library_path / ".manifest.json"

def load_manifest(library_path):
    path = get_manifest_path(library_path)
    if path.exists():
        return json.loads(path.read_text())
    return {}
```

---

## Agent Workflow

The autonomous agent follows a six-phase research workflow:

### Phase 0: Argument Dissection

**Purpose**: Map the logical structure of the research argument before searching.

**Process**:
1. Extract thesis from topic
2. Decompose into 3-7 supporting claims
3. For each claim, identify:
   - Evidence requirements
   - Counter-arguments to address
   - Logical dependencies

**Output**: JSON argument map

```json
{
  "thesis": "Attention mechanisms transformed NLP",
  "claims": [
    {
      "id": "C1",
      "claim": "RNNs have sequential bottleneck",
      "evidence_needed": ["architectural analysis", "complexity theory"],
      "counter_arguments": ["Parallel RNN variants"],
      "dependencies": []
    }
  ]
}
```

### Phase 1: Library Consultation

**Purpose**: Query existing knowledge before external search.

**Tools Used**:
- `list_library()` - Enumerate available papers
- `query_library(question)` - RAG query for each sub-question

**Output**: Gap analysis identifying which claims have sufficient evidence.

### Phase 2: Discovery and Acquisition

**Purpose**: Find and download papers to fill knowledge gaps.

**Discovery Strategies**:

| Strategy | Tool | Use Case |
|----------|------|----------|
| Keyword Search | `discover_papers(query)` | Topic-based discovery |
| Forward Citations | `discover_papers(cited_by=id)` | What cites a key paper |
| Backward Citations | `discover_papers(references=id)` | What a paper cites |
| Neural Search | `exa_search(query)` | Conceptual queries |

**Acquisition Flow**:

```
discover_papers() → add_paper() → query_library()
                         ↓
                  [PDF Downloaded]
                         ↓
                  [Indexed to Qdrant]
                         ↓
                  [Available for RAG]
```

### Phase 3: Multi-Pass Drafting

**Purpose**: Generate document through staged process.

**Pass 1 - Outline**:
- Create section structure
- Map evidence to each point
- Verify citation availability

**Pass 2 - Section Drafting**:
- Write each section independently
- Validate citations per section
- Cross-check claims with RAG

**Pass 3 - Integration**:
- Combine sections
- Add transitions
- Final coherence check

### Phase 4: Self-Critique

**Purpose**: Internal quality review before peer review.

**Checklist**:
1. Citation density (3-5 per paragraph)
2. Counter-argument coverage
3. Evidence verification via `query_library()`
4. Logical flow assessment
5. Final citation validation

### Phase 5: Peer Review

**Purpose**: External validation by separate LLM agent.

**Reviewer Configuration**:
- Same model as main agent
- Restricted toolset (read-only)
- Lower temperature (0.3) for rigorous checking

**Reviewer Tools**:
- `query_library()` - Verify claims
- `validate_citations()` - Check keys exist
- `fuzzy_cite()` - Look up papers
- `list_library()` - See available papers
- `discover_papers()` - Find missing references

**Review Output**:
```
VERDICT: [Accept | Minor Revisions | Major Revisions | Reject]
SUMMARY: Brief assessment
STRENGTHS: [...]
WEAKNESSES: [...]
RECOMMENDED PAPERS: [...]
SPECIFIC EDITS: [...]
```

### Phase 6: Revision

**Purpose**: Address peer review feedback.

**Process**:
1. Parse review feedback
2. Create revision plan
3. Execute revisions with tool access
4. Maintain citation quality

**Iteration**: Loop back to Phase 5 until Accept or max rounds reached.

---

## Tool Specifications

### Discovery Tools

#### `discover_papers(query, limit, cited_by, references)`

Search for academic papers using multiple sources.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | None | Keyword search query |
| `limit` | int | 15 | Maximum results |
| `cited_by` | str | None | Paper ID for forward citations |
| `references` | str | None | Paper ID for backward citations |

**Returns**: List of paper metadata (title, authors, abstract, DOI, arXiv ID, citation count)

**Sources**:
1. Semantic Scholar API (primary)
2. paper-scraper (PubMed, bioRxiv, Springer)
3. Private sources (if configured)

#### `exa_search(query, limit)`

Neural search via Exa.ai.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Natural language query |
| `limit` | int | 5 | Maximum results |

**Cost**: Uses Exa.ai API credits. Use sparingly.

### Acquisition Tools

#### `add_paper(identifier, source)`

Download paper to library.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identifier` | str | required | DOI or arXiv ID |
| `source` | str | "auto" | "doi", "arxiv", or "auto" |

**Acquisition Order**:
1. Private sources (if configured, highest success rate)
2. ArXiv (for preprints)
3. Unpaywall (open access)

**Side Effects**:
- Downloads PDF to Papis library
- Updates `master.bib`
- Triggers Qdrant indexing on next query

### RAG Tools

#### `query_library(question, paper_filter)`

Ask research questions using RAG.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `question` | str | required | Natural language question |
| `paper_filter` | str | None | Optional keyword filter |

**Returns**: Dict with `answer` text and list of `sources` used.

#### `list_library()`

Enumerate all papers in the library.

**Returns**: List of papers with citation_key, title, authors, year.

### Citation Tools

#### `fuzzy_cite(query)`

Fuzzy search for citation keys.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Author, title, year, or keyword |

**Returns**: List of matching papers with citation keys that exist in `master.bib`.

**Matching**: Fuzzy matching on author names, titles, and years.

#### `validate_citations(citation_keys)`

Validate citation keys before writing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `citation_keys` | List[str] | required | Keys to validate |

**Returns**: Dict with `valid` keys, `invalid` keys, and `suggestions` for invalid ones.

---

## Output Generation

### Typst Template System

Reports use a custom Typst template located at `templates/typst-template/`.

**Files**:
- `lib.typ` - Main template with project function
- `main.typ` - Generated document content

**Template Usage**:

```typst
#import "lib.typ": project

#show: project.with(
  title: "Short Title",
  subtitle: "Detailed Subtitle",
  authors: ("Research Agent",),
  date: "December 2025",
  abstract: [
    Abstract content...
  ]
)

#outline(indent: auto)
#pagebreak()

= Introduction
Content with @citation_key references...

#bibliography("refs.bib")
```

### Bibliography Management

1. **master.bib**: Complete library bibliography (Papis-managed)
2. **refs.bib**: Filtered to only cited papers (generated per report)

**Filtering Process**:

```python
def filter_bibtex_to_cited(master_bib_path, cited_keys):
    content = master_bib_path.read_text()
    pattern = r'(@\w+\{([^,]+),.*?\n\})'
    matches = re.findall(pattern, content, re.DOTALL)
    
    filtered = []
    for full_entry, key in matches:
        if key.strip() in cited_keys:
            filtered.append(full_entry)
    
    return "\n\n".join(filtered)
```

### PDF Compilation

Typst compilation with auto-fix for common errors:

```python
def compile_and_fix(typ_path, max_attempts=3):
    for attempt in range(max_attempts):
        result = subprocess.run(
            ["typst", "compile", typ_path.name],
            cwd=typ_path.parent,
            capture_output=True
        )
        if result.returncode == 0:
            return True
        
        # Auto-fix common issues
        fix_typst_error(typ_path, result.stderr)
```

**Auto-Fixes**:
- Double asterisks (`**`) → Single asterisk (`*`)
- Missing citation labels → Remove hallucinated keys
- Wrong bibliography filename → Correct to `refs.bib`

---

## Configuration Reference

### Iteration Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_AGENT_ITERATIONS` | 50 | Main agent loop limit |
| `MAX_REVISION_ITERATIONS` | 25 | Revision phase limit |
| `MAX_REVIEWER_ITERATIONS` | 15 | Reviewer tool call limit |

### Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `API_TIMEOUT_SECONDS` | 120 | Per-API-call timeout |
| `REVIEWER_TIMEOUT_SECONDS` | 180 | Reviewer session timeout |
| `MAX_SESSION_DURATION` | 14400 | Total session limit (4 hours) |

### Paths

| Path | Purpose |
|------|---------|
| `library/` | Papis PDF library |
| `library/.qdrant/` | Qdrant vector database |
| `library/.manifest.json` | Index tracking manifest |
| `reports/` | Generated research outputs |
| `master.bib` | Master bibliography |
| `templates/typst-template/` | Typst template files |

---

## Debugging

### Debug Logging

Each research session creates a debug log at `reports/<session>/artifacts/debug.log`.

**Log Format**:
```
2025-12-11 10:30:45 | DEBUG | Starting research session: topic
2025-12-11 10:30:46 | DEBUG | Research plan created: {...}
```

### Checkpoints

Progress checkpoints are saved to `reports/<session>/artifacts/checkpoint.json`:

```json
{
  "phase": "peer_review_r1",
  "timestamp": "2025-12-11T10:45:00",
  "data": {
    "round": 1,
    "reviews": [...],
    "verdict": "minor_revisions"
  }
}
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Empty RAG responses | No papers indexed | Run `research qa` to trigger indexing |
| Typst compilation errors | Markdown in Typst | Check for `**` instead of `*` |
| Citation not found | Key not in master.bib | Run `research add <id>` for the paper |
| Session timeout | Exceeded 4 hour limit | Increase `MAX_SESSION_DURATION` |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Dec 2025 | Argument dissection, multi-pass drafting, self-critique |
| 1.0 | Nov 2024 | Initial release with RAG-first workflow |
