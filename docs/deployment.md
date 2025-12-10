# Deployment Strategy: "Index Local, Deploy Global"

Running memory-intensive research agents (10GB+ RAM during indexing) on free cloud tiers requires a split architecture strategy.

## The Core Challenge

| Operation | Memory Usage | Frequency | Constraints |
|-----------|--------------|-----------|-------------|
| **Indexing** | ~10 GB | Once per library update | Exceeds most free tiers (512MB-1GB) |
| **Querying** | ~500 MB - 1 GB | Every run | Fits within generous free tiers |

## Solution: Split Architecture

Instead of running the full pipeline in the cloud, split the workload:

1.  **Index Locally (Heavy Lift)**
    *   Run `research batch-add` on your local machine (Mac).
    *   This builds the heavy vector index and `docs.pkl` cache.
    *   **Result:** A portable `library/.qa_cache/docs.pkl` file (~10-50MB).

2.  **Deploy Cache (Lightweight)**
    *   Upload the `docs.pkl` to your cloud environment.
    *   The agent detects the existing cache and skips the heavy indexing step.
    *   **Result:** Agent starts instantly with low memory footprint.

## Free Cloud Options

### 1. Hugging Face Spaces (Best Overall) 🏆
*   **Tier:** Free "CPU Upgrade" (2 vCPU, 16GB RAM) - *Wait, check current free tier, usually 2vCPU/16GB is standard for Spaces*
*   **Pros:** Generous RAM, persistent storage, easy deployment via Docker.
*   **Cons:** Container sleeps after inactivity (cold starts).
*   **Setup:**
    1. Create new Space (Docker SDK).
    2. Add `docs.pkl` to the repo (via Git LFS).
    3. Run agent script.

### 2. Google Colab (Best for Ad-hoc Runs) ⚡
*   **Tier:** Free T4 GPU Runtime (12GB+ System RAM).
*   **Pros:** Massive resources for short sessions (up to 12 hours).
*   **Cons:** Non-persistent files (must download results), sessions timeout.
*   **Setup:**
    1. Upload particular notebook.
    2. Mount Google Drive for `library/` persistence.
    3. Run agent interactively.

### 3. Google Cloud Run (Serverless) ☁️
*   **Tier:** 2GB RAM free tier (if optimized).
*   **Pros:** Scale to zero (no cost when not running).
*   **Cons:** 2GB might be tight for larger libraries.
*   **Setup:** Deploy minimal Docker container with pre-baked cache.

## Recommended Workflow (Hugging Face)

1. **Local:** Add papers & build index.
   ```bash
   python scripts/tools/library.py # runs indexing
   ```
2. **Commit:** Add the cache file to git.
   ```bash
   git add library/.qa_cache/docs.pkl
   git commit -m "Update research index"
   ```
3. **Deploy:** Push to Hugging Face Space.
   ```bash
   git push space main
   ```
4. **Run:** Trigger agent via API or simple UI.

## Docker Optimization (Dockerfile)

```dockerfile
# Use slim python image
FROM python:3.11-slim

WORKDIR /app

# Copy index ONLY (skips heavy PDF processing deps)
COPY library/.qa_cache /app/library/.qa_cache
COPY scripts /app/scripts
COPY requirements.txt .

# Install ONLY query dependencies (lighter than full indexing deps)
RUN pip install -r requirements.txt

# Run agent
CMD ["python", "scripts/agent.py"]
```
