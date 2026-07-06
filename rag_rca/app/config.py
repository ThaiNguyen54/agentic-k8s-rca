# app/config.py
from pathlib import Path

DATA_DIR = Path("data")
FILE_PATTERNS = ["**/*.yaml", "**/*.yml"]

QDRANT_MODE = "local"                  # "local" or "server"
QDRANT_PATH = "db/qdrant_rca"          # used when QDRANT_MODE="local"
QDRANT_URL = "http://localhost:6333"   # used when QDRANT_MODE="server"

COLLECTION_NAME = "rca_chunks"
EMBEDDING_MODEL = "BAAI/bge-m3"
TOP_K = 4
BATCH_SIZE = 64

# ── Evidence-First Retrieval Pipeline ────────────────────────────────────────
# The reranker paired with bge-m3. Loaded lazily only when USE_RERANKER_MODEL is
# True; otherwise the pipeline falls back to a deterministic lexical + signal
# scorer so it runs anywhere (no GPU, no model download).
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
USE_RERANKER_MODEL = False

# Stage 1 — how many candidate snippets to keep per evidence source after the
# cheap lexical prefilter (signal snippets are always kept regardless of this).
EVIDENCE_CANDIDATES_PER_SOURCE = 12

# Stage 3 — how many top snippets survive as verbatim evidence; the rest are
# collapsed into compact summaries.
EVIDENCE_FINAL_K = 8

# Stage 3 — when collapsing a cluster of near-identical routine lines, keep at
# most this many representative samples.
COMPRESSION_SAMPLES_PER_CLUSTER = 2

# Stage 4 — place the strongest snippet first and the second-strongest last.
EDGE_PLACEMENT_ENABLED = True

# Stage 5 — if the top reranked score is below this, the pipeline flags the
# incident as low-confidence so the agent knows to request the raw timeline.
LOW_CONFIDENCE_THRESHOLD = 0.35
