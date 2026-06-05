"""Optional transcript fetching. NEVER blocks the build.

- Hard 30s timeout per video, parallel workers.
- Reuses transcripts already fetched by legacy companion projects.
- Failure is fine: source_type='no_transcript_available' and we move on.
"""
import concurrent.futures
import json
import logging

from .config import LEGACY_TRANSCRIPT_DIRS, TRANSCRIPT_TIMEOUT_SECONDS, TRANSCRIPT_WORKERS
from .db import get_db, now, upsert

log = logging.getLogger("creator_twin.transcripts")


def _load_legacy_transcript(video_id: str):
    """Reuse transcripts preserved from the old ingest pipeline."""
    for d in LEGACY_TRANSCRIPT_DIRS:
        f = d / f"{video_id}.json"
        if f.exists():
            try:
                data = json.loads(f.read_text())
                segments = data.get("segments", data if isinstance(data, list) else [])
                text = " ".join(s.get("text", "") for s in segments).strip()
                if text:
                    return text
            except Exception:
                pass
    return None


def _fetch_one(video_id: str):
    """Fetch a transcript with the youtube-transcript-api (new and old API styles)."""
    legacy = _load_legacy_transcript(video_id)
    if legacy:
        return legacy, "legacy_cache"
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, "library_not_installed"
    try:
        try:  # new-style API (>=1.0)
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US", "en-GB"])
            text = " ".join(s.text for s in fetched).strip()
        except AttributeError:  # old-style API
            segs = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
            text = " ".join(s["text"] for s in segs).strip()
        return (text, "fetched") if text else (None, "empty")
    except Exception as e:
        return None, type(e).__name__


def fetch_transcripts(creator_id: str, skip=False, progress=None) -> dict:
    """Fetch transcripts for deep-pass videos only. Returns counts."""
    stats = {"fetched": 0, "skipped": 0}
    with get_db() as db:
        vids = [r["video_id"] for r in db.execute(
            "SELECT video_id FROM videos WHERE creator_id=? AND selected_for_deep_pass=1",
            (creator_id,)).fetchall()]
        done = {r["video_id"] for r in db.execute(
            "SELECT video_id FROM video_content WHERE content_type='transcript' AND video_id IN (%s)"
            % ",".join("?" * len(vids)), vids).fetchall()} if vids else set()
    todo = [v for v in vids if v not in done]
    stats["fetched"] = len(done)

    if skip:
        stats["skipped"] = len(todo)
        _mark_unavailable(todo, "skipped_by_flag")
        return stats

    if not todo:
        return stats

    with concurrent.futures.ThreadPoolExecutor(max_workers=TRANSCRIPT_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, v): v for v in todo}
        for fut in concurrent.futures.as_completed(futures):
            vid = futures[fut]
            try:
                text, status = fut.result(timeout=TRANSCRIPT_TIMEOUT_SECONDS)
            except Exception as e:
                text, status = None, type(e).__name__
            if text:
                stats["fetched"] += 1
                with get_db() as db:
                    db.execute(
                        "INSERT INTO video_content (video_id, content_type, source_type, confidence, text, synthetic_generated, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (vid, "transcript", "real_transcript", "high", text, 0, now(), now()))
                    db.execute("UPDATE videos SET transcript_status='fetched' WHERE video_id=?", (vid,))
            else:
                stats["skipped"] += 1
                _mark_unavailable([vid], status)
            if progress:
                progress(f"Transcripts: {stats['fetched']} fetched, {stats['skipped']} skipped")
    return stats


def _mark_unavailable(video_ids, reason):
    if not video_ids:
        return
    with get_db() as db:
        for vid in video_ids:
            db.execute("UPDATE videos SET transcript_status=? WHERE video_id=?",
                       (f"unavailable:{reason}", vid))


def get_transcript(video_id: str):
    with get_db() as db:
        row = db.execute(
            "SELECT text FROM video_content WHERE video_id=? AND content_type='transcript' ORDER BY id DESC LIMIT 1",
            (video_id,)).fetchone()
        return row["text"] if row else None
