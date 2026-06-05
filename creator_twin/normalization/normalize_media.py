"""Persist content_media rows (transcripts, OCR, media analysis per item)."""
import json

from ..db import get_db, new_id, now


def save_media(content_id: str, media_type: str, *, media_url="", local_path="",
               transcript_text="", ocr_text="", alt_text="", duration_seconds=0,
               source_type="public_metadata", confidence="medium", analysis=None) -> str:
    media_id = new_id("md")
    with get_db() as db:
        db.execute(
            """INSERT INTO content_media
               (media_id, content_id, media_type, media_url, local_path, transcript_text,
                ocr_text, alt_text, duration_seconds, media_analysis_json, source_type,
                confidence, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (media_id, content_id, media_type, media_url, local_path,
             transcript_text[:300000], ocr_text[:50000], alt_text[:2000],
             int(duration_seconds or 0), json.dumps(analysis or {}), source_type,
             confidence, now(), now()))
    return media_id


def get_transcript_for_content(content_id: str):
    with get_db() as db:
        row = db.execute(
            "SELECT transcript_text FROM content_media WHERE content_id=? AND transcript_text != '' "
            "ORDER BY created_at DESC LIMIT 1", (content_id,)).fetchone()
        return row["transcript_text"] if row else None
