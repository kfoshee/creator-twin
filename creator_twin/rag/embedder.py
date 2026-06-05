"""Embedding/index layer.

Current backend: SQLite FTS5/BM25 (zero keys, instant). The chunk schema
tracks embedding_status so a vector backend (EMBEDDING_MODEL env var) can be
swapped in later without re-chunking.
"""
import logging
import os

from ..db import get_db

log = logging.getLogger("creator_twin.rag.embedder")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "fts5_bm25")


def index_pending(creator_id: str) -> int:
    """FTS rows are written at chunk time; this marks status + future vector hook."""
    with get_db() as db:
        n = db.execute(
            "SELECT COUNT(*) AS n FROM rag_chunks WHERE creator_id=? AND embedding_status='indexed_fts'",
            (creator_id,)).fetchone()["n"]
    if EMBEDDING_MODEL != "fts5_bm25":
        log.info("EMBEDDING_MODEL=%s set — vector backend not yet wired, FTS5 active", EMBEDDING_MODEL)
    return n
