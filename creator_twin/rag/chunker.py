"""Build RAG chunks from every source: fingerprint, platform summaries,
content summaries, real text/transcripts, comments, QAs, style/audience/
commerce models, uploads. Each chunk carries platform/source_type/confidence."""
import json
import logging

from ..config import CHUNK_OVERLAP, CHUNK_SIZE, PROFILE_DIR
from ..creator_fingerprint import get_fingerprint
from ..db import get_db, new_id, now

log = logging.getLogger("creator_twin.rag.chunker")


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else end
    return [c for c in chunks if len(c) > 40]


def _add(db, creator_id, chunk_type, source_type, confidence, text,
         content_id=None, platform=None, metadata=None):
    cid = new_id("ch")
    db.execute(
        "INSERT INTO rag_chunks (chunk_id, creator_id, video_id, platform, chunk_type, source_type, confidence, text, metadata_json, embedding_status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, creator_id, content_id, platform, chunk_type, source_type, confidence,
         text, json.dumps(metadata or {}), "indexed_fts", now()))
    db.execute("INSERT INTO rag_fts (text, chunk_id, creator_id) VALUES (?,?,?)",
               (text, cid, creator_id))
    return 1


def build_chunks(creator_id: str, progress=None) -> int:
    count = 0
    profile = get_fingerprint(creator_id)

    with get_db() as db:
        old = [r["chunk_id"] for r in db.execute(
            "SELECT chunk_id FROM rag_chunks WHERE creator_id=?", (creator_id,)).fetchall()]
        db.execute("DELETE FROM rag_chunks WHERE creator_id=?", (creator_id,))
        for c in old:
            db.execute("DELETE FROM rag_fts WHERE chunk_id=?", (c,))

        # 1. fingerprint (approved profile wins; high confidence if approved)
        if profile:
            meta = profile.get("_meta", {})
            conf = "high" if meta.get("approved_by_creator") else meta.get("confidence", "medium")
            src = meta.get("source_type", "ai_inferred_creator_fingerprint")
            for key, val in profile.items():
                if key == "_meta" or not val:
                    continue
                text = f"Creator profile — {key.replace('_', ' ')}: " + (
                    json.dumps(val) if isinstance(val, dict)
                    else "; ".join(str(x) for x in val) if isinstance(val, list) else str(val))
                count += _add(db, creator_id, "creator_profile", src, conf, text[:3000])

        # 2. platform summaries
        for r in db.execute("SELECT platform, summary_json, confidence FROM platform_summaries WHERE creator_id=?",
                            (creator_id,)).fetchall():
            s = json.loads(r["summary_json"])
            text = f"{r['platform']} presence: " + "; ".join(
                f"{k.replace('_', ' ')}: {json.dumps(v) if isinstance(v, (list, dict)) else v}"
                for k, v in s.items() if v)
            count += _add(db, creator_id, "platform_summary", "ai_inferred_platform_summary",
                          r["confidence"], text[:3000], platform=r["platform"])

        # 3. content summaries + real text
        rows = db.execute(
            """SELECT cs.summary_json, cs.source_type AS s_src, cs.confidence AS s_conf,
                      ci.content_id, ci.platform, ci.title, ci.caption, ci.canonical_url, ci.text_body,
                      ci.source_type AS c_src, ci.content_type
               FROM content_summaries cs JOIN content_items ci ON ci.content_id=cs.content_id
               WHERE ci.creator_id=?""", (creator_id,)).fetchall()
        summarized = set()
        for r in rows:
            summarized.add(r["content_id"])
            s = json.loads(r["summary_json"])
            label = r["title"] or (r["caption"] or "")[:80] or r["content_type"]
            meta = {"title": label, "url": r["canonical_url"], "platform": r["platform"]}
            text = (f"[{r['platform']}] \"{label}\" ({r['canonical_url']})\n"
                    + "\n".join(f"{k.replace('_', ' ')}: {v if not isinstance(v, list) else '; '.join(map(str, v))}"
                                for k, v in s.items() if v and k != "content_id"))
            count += _add(db, creator_id, "content_notes", r["s_src"], r["s_conf"], text[:3000],
                          r["content_id"], r["platform"], meta)
            if r["text_body"]:
                src = "real_transcript" if r["c_src"] == "real_transcript" else r["c_src"]
                for piece in chunk_text(r["text_body"]):
                    count += _add(db, creator_id, "real_text_chunk", src, "high",
                                  f"[{r['platform']}] From \"{label}\": {piece}",
                                  r["content_id"], r["platform"], meta)

        # 3b. real text for items without summaries (uploads, web pages before catalog)
        for r in db.execute(
                """SELECT content_id, platform, title, canonical_url, text_body, source_type
                   FROM content_items WHERE creator_id=? AND text_body != ''""", (creator_id,)).fetchall():
            if r["content_id"] in summarized:
                continue
            meta = {"title": r["title"], "url": r["canonical_url"], "platform": r["platform"]}
            for piece in chunk_text(r["text_body"]):
                count += _add(db, creator_id, "real_text_chunk", r["source_type"], "high",
                              f"[{r['platform']}] From \"{r['title']}\": {piece}",
                              r["content_id"], r["platform"], meta)

        # 4. comments grouped per content item
        for r in db.execute(
                """SELECT c.content_id, ci.platform, ci.title, GROUP_CONCAT(c.text, ' || ') AS texts
                   FROM comments c JOIN content_items ci ON ci.content_id=c.content_id
                   WHERE ci.creator_id=? GROUP BY c.content_id""", (creator_id,)).fetchall():
            count += _add(db, creator_id, "audience_comments", "platform_comments", "high",
                          f"Audience comments on \"{(r['title'] or '')[:80]}\": {r['texts'][:1500]}",
                          r["content_id"], r["platform"], {"title": r["title"]})

        # 5. QA pairs
        for r in db.execute("SELECT * FROM qa_pairs WHERE creator_id=?", (creator_id,)).fetchall():
            count += _add(db, creator_id, "qa_pair", r["source_type"], r["confidence"],
                          f"Q: {r['question']}\nA: {r['answer']}")

        # 6. style / audience / commerce models
        for kind in ("style_model", "audience_model", "commerce"):
            f = PROFILE_DIR / f"{creator_id}_{kind}.json"
            if f.exists():
                model = json.loads(f.read_text())
                for key, val in model.items():
                    if not val:
                        continue
                    text = f"Creator {kind.replace('_', ' ')} — {key.replace('_', ' ')}: " + (
                        json.dumps(val) if isinstance(val, (dict, list)) else str(val))
                    count += _add(db, creator_id, kind, f"ai_inferred_{kind}", "medium", text[:3000])

        if progress:
            progress(f"Indexed {count} chunks")
    return count
