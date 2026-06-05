"""Retrieval over the FTS5 index with confidence-aware ranking."""
import json
import re

from .db import get_db

CONF_BOOST = {"high": 0.0, "medium": 0.6, "low": 1.2}  # added to bm25 (lower = better)


def _fts_query(question: str) -> str:
    terms = re.findall(r"[a-zA-Z0-9']{3,}", question.lower())
    stop = {"the", "and", "for", "what", "who", "this", "that", "with", "should",
            "would", "their", "your", "you", "are", "does", "how", "which", "about"}
    terms = [t for t in terms if t not in stop][:12]
    return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


def search(creator_id: str, question: str, k: int = 12) -> list:
    query = _fts_query(question)
    with get_db() as db:
        try:
            rows = db.execute(
                """SELECT c.chunk_id, c.chunk_type, c.source_type, c.confidence, c.text,
                          c.metadata_json, c.video_id, bm25(rag_fts) AS rank
                   FROM rag_fts JOIN rag_chunks c ON c.chunk_id = rag_fts.chunk_id
                   WHERE rag_fts MATCH ? AND rag_fts.creator_id = ?
                   ORDER BY rank LIMIT ?""", (query, creator_id, k * 3)).fetchall()
        except Exception:
            rows = []
        if not rows:  # fallback: highest-signal profile + top video notes
            rows = db.execute(
                """SELECT chunk_id, chunk_type, source_type, confidence, text, metadata_json,
                          video_id, 0 AS rank
                   FROM rag_chunks WHERE creator_id=? AND chunk_type IN ('creator_profile','video_notes')
                   LIMIT ?""", (creator_id, k)).fetchall()
    results = []
    for r in rows:
        score = (r["rank"] or 0) + CONF_BOOST.get(r["confidence"], 0.6)
        results.append({
            "chunk_id": r["chunk_id"], "chunk_type": r["chunk_type"],
            "source_type": r["source_type"], "confidence": r["confidence"],
            "text": r["text"], "video_id": r["video_id"],
            "metadata": json.loads(r["metadata_json"] or "{}"), "score": score,
        })
    results.sort(key=lambda x: x["score"])
    return results[:k]
