"""Fetch top comments for deep-pass videos via the YouTube Data API.

Comments reveal what the audience asks, struggles with, and praises —
high signal for the creator fingerprint, cheap to fetch.
"""
import logging

from .db import get_db, now
from .youtube_metadata import YouTubeError, api_get

log = logging.getLogger("creator_twin.comments")


def fetch_comments(creator_id: str, per_video=20, force_refresh=False, progress=None) -> int:
    fetched = 0
    with get_db() as db:
        vids = [r["video_id"] for r in db.execute(
            "SELECT video_id FROM videos WHERE creator_id=? AND selected_for_deep_pass=1 AND comment_count > 0",
            (creator_id,)).fetchall()]
    for i, vid in enumerate(vids):
        try:
            data = api_get("commentThreads", {
                "part": "snippet", "videoId": vid, "order": "relevance",
                "maxResults": per_video, "textFormat": "plainText",
            }, force_refresh)
        except YouTubeError as e:
            log.warning("Comments unavailable for %s: %s", vid, e)
            continue
        with get_db() as db:
            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]["snippet"]
                db.execute(
                    "INSERT OR REPLACE INTO comments (comment_id, video_id, author_name, text, like_count, published_at, is_top_comment, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (item["id"], vid, top.get("authorDisplayName", ""),
                     top.get("textDisplay", "")[:2000], int(top.get("likeCount", 0) or 0),
                     top.get("publishedAt", ""), 1, now()))
                fetched += 1
        if progress and (i + 1) % 5 == 0:
            progress(f"Comments: {fetched} fetched across {i + 1}/{len(vids)} videos")
    return fetched


def top_comments_text(creator_id: str, limit=120) -> str:
    """A digest of top audience comments for LLM context."""
    with get_db() as db:
        rows = db.execute(
            """SELECT c.text, c.like_count, v.title FROM comments c
               JOIN videos v ON v.video_id = c.video_id
               WHERE v.creator_id=? ORDER BY c.like_count DESC LIMIT ?""",
            (creator_id, limit)).fetchall()
    return "\n".join(f"[on \"{r['title'][:60]}\", {r['like_count']} likes] {r['text'][:300]}" for r in rows)
