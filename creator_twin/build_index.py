"""Build the RAG index from all sources into rag_chunks + SQLite FTS5.

FTS5/BM25 keyword retrieval works with zero extra dependencies or embedding
keys; swap in vector embeddings later without changing the chunk schema
(embedding_status tracks that).
"""
import argparse
import json
import logging

from .config import CHUNK_OVERLAP, CHUNK_SIZE
from .creator_fingerprint import get_fingerprint
from .db import get_db, new_id, now

log = logging.getLogger("creator_twin.index")


def _chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # break at sentence/space when possible
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else end
    return [c for c in chunks if len(c) > 40]


def _add_chunk(db, creator_id, chunk_type, source_type, confidence, text, video_id=None, metadata=None):
    cid = new_id("ch")
    db.execute(
        "INSERT INTO rag_chunks (chunk_id, creator_id, video_id, chunk_type, source_type, confidence, text, metadata_json, embedding_status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, creator_id, video_id, chunk_type, source_type, confidence, text,
         json.dumps(metadata or {}), "indexed_fts", now()))
    db.execute("INSERT INTO rag_fts (text, chunk_id, creator_id) VALUES (?,?,?)", (text, cid, creator_id))
    return cid


def build_index(creator_id: str, progress=None) -> int:
    count = 0
    profile = get_fingerprint(creator_id)

    with get_db() as db:
        # wipe old index for this creator (rebuild is cheap and deterministic)
        old = [r["chunk_id"] for r in db.execute(
            "SELECT chunk_id FROM rag_chunks WHERE creator_id=?", (creator_id,)).fetchall()]
        db.execute("DELETE FROM rag_chunks WHERE creator_id=?", (creator_id,))
        if old:
            db.executemany("DELETE FROM rag_fts WHERE chunk_id=?", [(c,) for c in old])

        creator = dict(db.execute("SELECT * FROM creators WHERE creator_id=?", (creator_id,)).fetchone())

        # 1. creator fingerprint chunks
        if profile:
            meta = profile.get("_meta", {})
            src = meta.get("source_type", "ai_inferred_creator_fingerprint")
            conf = "high" if meta.get("approved_by_creator") else meta.get("confidence", "medium")
            for key, val in profile.items():
                if key == "_meta" or not val:
                    continue
                text = f"Creator profile — {key.replace('_', ' ')}: " + (
                    "; ".join(str(x) for x in val) if isinstance(val, list) else str(val))
                count += bool(_add_chunk(db, creator_id, "creator_profile", src, conf, text))

        # 2. channel summary
        if creator.get("channel_description"):
            text = f"Channel: {creator['channel_title']}. {creator['channel_description'][:1500]}"
            count += bool(_add_chunk(db, creator_id, "channel_summary", "channel_metadata", "high", text))

        # 3. per-video catalog notes + transcript chunks
        rows = db.execute(
            """SELECT vc.*, v.title, v.video_url, v.view_count FROM video_content vc
               JOIN videos v ON v.video_id = vc.video_id WHERE v.creator_id=?""",
            (creator_id,)).fetchall()
        for r in rows:
            meta = {"title": r["title"], "url": r["video_url"]}
            if r["content_type"] == "catalog_notes":
                text = f"Video: \"{r['title']}\" ({r['video_url']})\n{r['text']}"
                count += bool(_add_chunk(db, creator_id, "video_notes", r["source_type"],
                                         r["confidence"], text, r["video_id"], meta))
            elif r["content_type"] == "transcript":
                for piece in _chunk_text(r["text"]):
                    text = f"From \"{r['title']}\" (transcript): {piece}"
                    count += bool(_add_chunk(db, creator_id, "transcript_chunk", "real_transcript",
                                             "high", text, r["video_id"], meta))
            if progress and count % 50 == 0:
                progress(f"Indexed {count} chunks")

        # 4. audience comments digest (grouped per video)
        comment_rows = db.execute(
            """SELECT c.video_id, v.title, GROUP_CONCAT(c.text, ' || ') AS texts
               FROM comments c JOIN videos v ON v.video_id=c.video_id
               WHERE v.creator_id=? GROUP BY c.video_id""", (creator_id,)).fetchall()
        for r in comment_rows:
            text = f"Audience comments on \"{r['title']}\": {r['texts'][:1500]}"
            count += bool(_add_chunk(db, creator_id, "audience_comments", "youtube_comments",
                                     "high", text, r["video_id"], {"title": r["title"]}))

        # 5. QA pairs
        for r in db.execute("SELECT * FROM qa_pairs WHERE creator_id=?", (creator_id,)).fetchall():
            text = f"Q: {r['question']}\nA: {r['answer']}"
            count += bool(_add_chunk(db, creator_id, "qa_pair", r["source_type"], r["confidence"], text))

    if progress:
        progress(f"Index complete: {count} chunks")
    log.info("Built index with %d chunks for %s", count, creator_id)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-id", required=True)
    args = ap.parse_args()
    n = build_index(args.creator_id)
    print(f"Indexed {n} chunks for {args.creator_id}")
