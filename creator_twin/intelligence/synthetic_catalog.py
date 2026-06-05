"""Universal synthetic catalog: content_summaries for EVERY content item.

Real text/transcript -> faithful summary (high). Metadata only -> honestly
labeled inferred notes (medium/low).
"""
import json
import logging

from ..creator_fingerprint import fingerprint_summary, get_fingerprint
from ..db import get_db, new_id, now
from ..llm import LLMError, complete_json
from ..prompts import CATALOG_SYSTEM, CONTENT_SUMMARY_PROMPT

log = logging.getLogger("creator_twin.intelligence.catalog")

RICH_BATCH = 4    # items with real text per LLM call
LIGHT_BATCH = 12  # metadata-only items per call


def generate_qa_pairs(creator_id: str, n=20) -> int:
    """Cross-platform fan Q&A pairs grounded in the catalog + comments."""
    from ..prompts import QA_PROMPT, QA_SYSTEM
    profile = get_fingerprint(creator_id) or {}
    with get_db() as db:
        notes = db.execute(
            """SELECT cs.summary_json, ci.title, ci.caption, ci.platform
               FROM content_summaries cs JOIN content_items ci ON ci.content_id=cs.content_id
               WHERE ci.creator_id=? ORDER BY ci.selected_for_deep_pass DESC LIMIT 40""",
            (creator_id,)).fetchall()
        comments = db.execute(
            """SELECT text, like_count FROM comments WHERE content_id IN
               (SELECT content_id FROM content_items WHERE creator_id=?)
               ORDER BY like_count DESC LIMIT 40""", (creator_id,)).fetchall()
    catalog = "\n".join(
        f"- [{r['platform']}] {(r['title'] or r['caption'] or '')[:80]} — "
        f"{json.loads(r['summary_json']).get('concise_summary', '')[:140]}" for r in notes)
    try:
        pairs = complete_json(QA_PROMPT.format(
            fingerprint=json.dumps({k: v for k, v in profile.items() if k != '_meta'})[:4000],
            catalog=catalog[:6000],
            comments="\n".join(f"[{c['like_count']}] {c['text'][:200]}" for c in comments)[:3000] or "(none)",
            n=n), system=QA_SYSTEM, max_tokens=6000)
    except LLMError as e:
        log.warning("QA generation failed: %s", e)
        return 0
    count = 0
    with get_db() as db:
        for p in pairs if isinstance(pairs, list) else []:
            if not p.get("question"):
                continue
            db.execute(
                "INSERT INTO qa_pairs (qa_id, creator_id, video_id, question, answer, source_type, confidence, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (new_id("qa"), creator_id, None, p["question"], p.get("answer", ""),
                 "ai_generated_qa", p.get("confidence", "medium"), now()))
            count += 1
    return count


def generate_catalog(creator_id: str, force_refresh=False, progress=None,
                     scope: str = "fast", batch_limit: int = None,
                     progress_label: str = "Catalog") -> int:
    """Summarize content items.

    scope: 'fast' = selected_for_fast_build only; 'pending' = unprocessed
    remainder (background enrichment); 'all' = everything.
    """
    profile = get_fingerprint(creator_id) or {}
    fp = fingerprint_summary(profile)

    where = {"fast": "AND selected_for_fast_build=1",
             "pending": "AND processing_status='pending'",
             "all": ""}[scope]
    with get_db() as db:
        items = [dict(r) for r in db.execute(
            f"""SELECT content_id, platform, content_type, title, caption, description, text_body
               FROM content_items WHERE creator_id=? {where}
               ORDER BY is_product_related DESC, selected_for_deep_pass DESC""",
            (creator_id,)).fetchall()]
        done = set() if force_refresh else {r["content_id"] for r in db.execute(
            "SELECT content_id FROM content_summaries").fetchall()}

    todo = [i for i in items if i["content_id"] not in done]
    if batch_limit:
        todo = todo[:batch_limit]
    rich = [i for i in todo if i["text_body"]]
    light = [i for i in todo if not i["text_body"]]
    batches = [rich[i:i + RICH_BATCH] for i in range(0, len(rich), RICH_BATCH)] + \
              [light[i:i + LIGHT_BATCH] for i in range(0, len(light), LIGHT_BATCH)]

    generated = 0
    for batch in batches:
        block = ""
        for it in batch:
            block += (f"\nCONTENT_ID: {it['content_id']}\nPLATFORM: {it['platform']} ({it['content_type']})"
                      f"\nTITLE/CAPTION: {(it['title'] or it['caption'] or '')[:200]}"
                      f"\nDESCRIPTION: {(it['description'] or '')[:400]}")
            if it["text_body"]:
                block += f"\nREAL_TEXT:\n{it['text_body'][:5000]}\n"
        try:
            notes = complete_json(CONTENT_SUMMARY_PROMPT.format(
                fingerprint_summary=fp, items_block=block), system=CATALOG_SYSTEM, max_tokens=8000)
        except LLMError as e:
            log.warning("catalog batch failed (%d items): %s", len(batch), e)
            with get_db() as db:
                for it in batch:
                    db.execute("UPDATE content_items SET processing_status='failed', processing_error=? WHERE content_id=?",
                               (str(e)[:300], it["content_id"]))
            continue
        if isinstance(notes, dict):
            notes = [notes]
        valid = {it["content_id"]: bool(it["text_body"]) for it in batch}
        with get_db() as db:
            for note in notes:
                cid = note.get("content_id", "")
                if cid not in valid:
                    continue
                has_text = valid[cid]
                db.execute("DELETE FROM content_summaries WHERE content_id=?", (cid,))
                db.execute(
                    "INSERT INTO content_summaries (summary_id, content_id, source_type, synthetic_generated, confidence, summary_json, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (new_id("cs"), cid,
                     "real_text_summary" if has_text else "metadata_inferred_notes",
                     0 if has_text else 1,
                     "high" if has_text else (note.get("confidence") or "medium"),
                     json.dumps(note), now(), now()))
                db.execute("UPDATE content_items SET processing_status='summarized', last_processed_at=? WHERE content_id=?",
                           (now(), cid))
                generated += 1
        if progress:
            progress(f"{progress_label}: {generated}/{len(todo)} items summarized")
    return generated
