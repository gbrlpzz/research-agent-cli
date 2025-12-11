# Limitations and Known Issues

This document provides an honest assessment of the current limitations, constraints, and known issues of the Research Agent CLI. Understanding these constraints helps set appropriate expectations and guides future development priorities.

## Table of Contents

- [API Dependencies](#api-dependencies)
- [PDF Acquisition](#pdf-acquisition)
- [Performance Considerations](#performance-considerations)
- [Content Processing](#content-processing)
- [Quality Constraints](#quality-constraints)
- [Known Issues](#known-issues)
- [Roadmap](#roadmap)

---

## API Dependencies

### Required: Google Gemini API

The system currently requires a Google Gemini API key. There is no support for:

- Local LLM models (Ollama, llama.cpp)
- Alternative cloud providers (OpenAI, Anthropic, Cohere)
- Self-hosted inference servers

**Impact**: Requires internet connectivity and incurs API costs for usage.

**Mitigation**: Gemini offers a generous free tier for development and moderate usage.

### Optional: Rate-Limited Services

| Service | Free Tier | Limitation |
|---------|-----------|------------|
| Semantic Scholar | 100 req/5min | Sufficient for most research |
| Exa.ai | Limited credits | Costs money for heavy usage |
| Unpaywall | Unlimited | Only open access papers |

**Impact**: Heavy discovery sessions may hit rate limits, causing delays.

**Mitigation**: The agent implements retry logic with exponential backoff.

---

## PDF Acquisition

### Open Access Coverage Gap

The default acquisition chain covers:

1. **ArXiv** - Preprints only (~2.4M papers)
2. **Unpaywall** - Open access versions (~40M papers)

This leaves a significant portion of academic literature inaccessible without additional configuration.

**Impact**: Many journal articles require subscription access not available through these sources.

**Mitigation**: Private source configuration (documented separately) can extend coverage significantly.

### Acquisition Failure Modes

| Scenario | Behavior |
|----------|----------|
| No PDF available | Paper metadata added but not indexed for RAG |
| Corrupted PDF | Blacklisted, skipped in future indexing |
| Password-protected PDF | Cannot be processed |
| Scanned PDF (image-only) | OCR not performed, limited utility |

---

## Performance Considerations

### Initial Indexing Time

First-time indexing of a large library can be slow:

| Library Size | Approximate Time |
|--------------|------------------|
| 10 PDFs | 2-5 minutes |
| 50 PDFs | 10-20 minutes |
| 100 PDFs | 30-60 minutes |
| 500+ PDFs | Several hours |

**Cause**: Each PDF must be chunked, embedded, and stored in Qdrant.

**Mitigation**: Indexing is incremental. Only new/changed files are processed after initial run.

### Memory Usage

Large libraries consume significant memory during indexing:

| Library Size | Peak RAM |
|--------------|----------|
| 50 PDFs | ~2 GB |
| 100 PDFs | ~4 GB |
| 500 PDFs | ~8-10 GB |

**Mitigation**: Process papers in batches if memory is constrained.

### Session Timeouts

The agent enforces a maximum session duration of 4 hours to prevent:

- Runaway API costs
- Infinite loops
- Resource exhaustion

**Impact**: Very large research tasks may not complete in a single session.

**Workaround**: Checkpoint system saves progress, though resume is not yet implemented.

---

## Content Processing

### Text-Only Extraction

The current implementation extracts text content only. It does not:

- Extract figures or images from PDFs
- Parse tables into structured data
- Recognize mathematical equations
- Process LaTeX source files

**Impact**: Information in figures and tables is not available to the RAG system.

### Language Support

Processing is English-centric:

- Prompts and instructions are in English
- Embedding model optimized for English
- Non-English papers may have reduced retrieval quality

**Impact**: Research in non-English literature may yield suboptimal results.

### Citation Parsing

BibTeX parsing uses regex patterns that may fail on:

- Non-standard BibTeX formatting
- Entries with unusual characters
- Multi-line fields with complex escaping

---

## Quality Constraints

### Citation Hallucination

Despite extensive validation, the LLM may occasionally:

- Generate plausible but non-existent citation keys
- Misattribute claims to the wrong papers
- Cite papers not actually in the library

**Mitigation**:
1. `validate_citations()` tool catches invalid keys
2. Peer review phase double-checks citations
3. Auto-fix removes unverified keys during compilation

**Residual Risk**: Some hallucinations may pass validation if they match existing key patterns.

### Evidence Quality

The RAG system retrieves relevant passages but cannot:

- Verify factual accuracy of source papers
- Detect retracted papers
- Assess methodological quality
- Identify conflicts of interest

**Impact**: The agent may synthesize information from low-quality sources.

### Argument Coherence

Multi-pass drafting improves coherence but may still produce:

- Redundant sections
- Logical inconsistencies between sections
- Abrupt transitions
- Uneven depth of coverage

---

## Known Issues

### Typst Compilation Edge Cases

| Issue | Description | Status |
|-------|-------------|--------|
| Unicode punctuation | Em-dashes render as escape codes | Mitigated by ASCII-only rule |
| Unbalanced delimiters | Odd number of `*` causes error | Auto-fix implemented |
| Long abstracts | May overflow cover page | Template adjustment needed |
| Special characters in titles | Can break compilation | Escaping needed |

### Vector Database

| Issue | Description | Workaround |
|-------|-------------|------------|
| Stale index | Deleted PDFs remain indexed | Manual Qdrant cleanup |
| Index corruption | Rare, after unclean shutdown | Delete `.qdrant/` folder |
| Duplicate embeddings | Same PDF added twice | Manifest deduplication |

### API Interactions

| Issue | Description | Mitigation |
|-------|-------------|------------|
| Empty responses | Gemini occasionally returns empty | Retry logic (3 attempts) |
| Timeout on long inference | Complex queries may timeout | Configurable timeout |
| Rate limiting | Burst requests may be throttled | Exponential backoff |

---

## Roadmap

The following features are planned to address current limitations:

### Near-Term (Q1 2025)

- [ ] **Resume from Checkpoint**: Continue interrupted sessions
- [ ] **Multi-Model Support**: OpenAI, Anthropic, local models
- [ ] **Improved Error Recovery**: Better handling of API failures
- [ ] **Batch Paper Addition**: Bulk import from BibTeX

### Medium-Term (Q2 2025)

- [ ] **Web Interface**: Browser-based report viewing and management
- [ ] **Figure Extraction**: Extract and reference images from PDFs
- [ ] **Table Parsing**: Structured extraction of tabular data
- [ ] **Multi-Language Support**: Non-English paper processing

### Long-Term

- [ ] **Collaborative Sessions**: Multi-user research projects
- [ ] **Version Control Integration**: Git-based report versioning
- [ ] **Custom Templates**: User-defined Typst templates
- [ ] **Plugin System**: Extensible tool architecture

---

## Reporting Issues

If you encounter issues not documented here:

1. Check `reports/<session>/artifacts/debug.log` for error details
2. Review `checkpoint.json` for session state
3. Open an issue on GitHub with:
   - Error message
   - Steps to reproduce
   - Relevant log excerpts

---

## Disclaimer

This is research software provided as-is. While we strive for accuracy:

- Generated documents require human review before publication
- Citations should be verified against original sources
- The agent may produce factually incorrect statements
- API costs are the user's responsibility

Always apply critical judgment to agent-generated outputs.
