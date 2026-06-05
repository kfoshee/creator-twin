"""Synthetic catalog brain: per-video learning notes for EVERY video.

Real transcript -> real_transcript_summary (high confidence).
No transcript -> metadata_inferred_notes (medium/low), honestly labeled.
Also generates creator-level QA pairs.
"""
import json
import logging

from .comments_fetch import top_comments_text
from .creator_fingerprint import fingerprint_summary, get_fingerprint
from .db import get_db, new_id, now
from .llm import LLMError, complete_json
from .prompts import CATALOG_PROMPT, CATALOG_SYSTEM, QA_PROMPT, QA_SYSTEM
from .transcript_optional import get_transcript

log = logging.getLogger("creator_twin.catalog")

DEEP_BATCH = 4     # videos with transcripts per LLM call
LIGHT_BATCH = 12   # metadata-only videos per LLM call


def _video_block(v, transcript):
    block = f"VIDEO_ID: {v['video_id']}\nTITLE: {v['title']}\nDESCRIPTION: {(v['description'] or '')[:600]}"
    if transcript:
        block += f"\nHAS_TRANSCRIPT:\n{transcript[:5000]}"
    return block


def generate_catalog(creator_id: str, force_refresh=False, progress=None) -> int:
    profile = get_fingerprint(creator_id) or {}
    fp_summary = fingerprint_summary(profile)

    with get_db() as db:
        videos = [dict(r) for r in db.execute(
            "SELECT video_id, title, description FROM videos WHERE creator_id=? ORDER BY view_count DESC",
            (creator_id,)).fetchall()]
        done = set() if force_refresh else {r["video_id"] for r in db.execute(
            "SELECT video_id FROM video_content WHERE content_type='catalog_notes'").fetchall()}

    todo = [v for v in videos if v["video_id"] not in done]
    generated = 0

    # split into transcript-rich (small batches) and metadata-only (bigger batches)
    with_t, without_t = [], []
    for v in todo:
        t = get_transcript(v["video_id"])
        (with_t if t else without_t).append((v, t))

    batches = [with_t[i:i + DEEP_BATCH] for i in range(0, len(with_t), DEEP_BATCH)] + \
              [without_t[i:i + LIGHT_BATCH] for i in range(0, len(without_t), LIGHT_BATCH)]

    for batch in batches:
        videos_block = "\n\n".join(_video_block(v, t) for v, t in batch)
        prompt = CATALOG_PROMPT.format(fingerprint_summary=fp_summary, videos_block=videos_block)
        try:
            notes = complete_json(prompt, system=CATALOG_SYSTEM, max_tokens=8000)
        except LLMError as e:
            log.warning("Catalog batch failed, skipping %d videos: %s", len(batch), e)
            continue
        if isinstance(notes, dict):
            notes = [notes]
        transcripts_by_id = {v["video_id"]: t for v, t in batch}
        with get_db() as db:
            for note in notes:
                vid = note.get("video_id", "")
                if vid not in transcripts_by_id:
                    continue
                has_t = bool(transcripts_by_id[vid])
                source = "real_transcript_summary" if has_t else "metadata_inferred_notes"
                conf = "high" if has_t else (note.get("confidence") or "medium")
                text = "\n".join(filter(None, [
                    note.get("concise_summary", ""), note.get("detailed_summary", ""),
                    "Main lesson: " + note.get("main_lesson", ""),
                    "Takeaways: " + "; ".join(note.get("practical_takeaways", [])),
                    "" if has_t else note.get("synthetic_transcript_style_notes", ""),
                ]))
                db.execute(
                    "INSERT INTO video_content (video_id, content_type, source_type, confidence, text, json_payload, synthetic_generated, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (vid, "catalog_notes", source, conf, text, json.dumps(note), 0 if has_t else 1, now(), now()))
                generated += 1
        if progress:
            progress(f"Synthetic catalog: {generated}/{len(todo)} videos annotated")
    return generated


def generate_qa_pairs(creator_id: str, n=20, progress=None) -> int:
    profile = get_fingerprint(creator_id) or {}
    with get_db() as db:
        notes = db.execute(
            """SELECT vc.json_payload, v.title FROM video_content vc
               JOIN videos v ON v.video_id = vc.video_id
               WHERE v.creator_id=? AND vc.content_type='catalog_notes'
               ORDER BY v.view_count DESC LIMIT 40""", (creator_id,)).fetchall()
    catalog = "\n".join(
        f"- {r['title']} — {json.loads(r['json_payload']).get('concise_summary', '')[:150]}"
        for r in notes)
    prompt = QA_PROMPT.format(
        fingerprint=json.dumps({k: v for k, v in profile.items() if k != "_meta"}, indent=1)[:4000],
        catalog=catalog[:6000],
        comments=top_comments_text(creator_id, limit=40)[:3000] or "(none)",
        n=n)
    try:
        pairs = complete_json(prompt, system=QA_SYSTEM, max_tokens=6000)
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
    if progress:
        progress(f"Generated {count} Q&A pairs")
    return count
