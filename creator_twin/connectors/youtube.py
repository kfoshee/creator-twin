"""YouTube connector — wraps the original fast-build pipeline.

Preserves all existing behavior (videos/comments tables, transcript flow)
and additionally normalizes everything into the universal content model.
"""
import json
import logging

from ..db import get_db
from .. import comments_fetch, transcript_optional, youtube_metadata
from ..normalization.normalize_media import save_media
from ..normalization.normalize_posts import content_id_for
from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.youtube")


class YouTubeConnector(BaseConnector):
    platform = "youtube"

    def validate_config(self):
        from ..config import YOUTUBE_API_KEY
        if not (self.config.get("channel_url") or self.config.get("channel_id")):
            self.errors.append("no channel_url/channel_id provided")
            return False
        if not YOUTUBE_API_KEY:
            self.errors.append("YOUTUBE_API_KEY not set")
            return False
        return True

    def fetch_profile(self):
        cid = self.config.get("channel_id") or youtube_metadata.resolve_channel_id(
            self.config["channel_url"], self.config.get("force_refresh", False))
        self.channel = youtube_metadata.fetch_channel(
            cid, self.config.get("channel_url", ""), self.config.get("force_refresh", False))
        # fetch_channel upserts a creators row keyed by channel_id; if it minted a
        # different creator_id (adding YouTube to an existing creator), merge it into ours.
        legacy = self.channel["creator_id"]
        if legacy != self.creator_id:
            with get_db() as db:
                row = db.execute("SELECT * FROM creators WHERE creator_id=?", (legacy,)).fetchone()
                if row:
                    # delete first — channel_id is UNIQUE, so the copy must come after
                    db.execute("DELETE FROM creators WHERE creator_id=?", (legacy,))
                    db.execute(
                        """UPDATE creators SET channel_id=?, channel_url=?, channel_title=?,
                           display_name=?, channel_description=?, custom_url=?, subscriber_count=?,
                           video_count=?, view_count=?, thumbnail_url=? WHERE creator_id=?""",
                        (row["channel_id"], row["channel_url"], row["channel_title"],
                         row["channel_title"], row["channel_description"], row["custom_url"],
                         row["subscriber_count"], row["video_count"], row["view_count"],
                         row["thumbnail_url"], self.creator_id))
                for table in ("videos", "creator_fingerprint", "rag_chunks", "qa_pairs"):
                    db.execute(f"UPDATE {table} SET creator_id=? WHERE creator_id=?",
                               (self.creator_id, legacy))
            self.channel["creator_id"] = self.creator_id
        return self.channel

    def normalize_profile(self, raw):
        with get_db() as db:
            c = db.execute("SELECT * FROM creators WHERE creator_id=?", (self.creator_id,)).fetchone()
        return {
            "platform_user_id": raw["channel_id"], "handle": (c["custom_url"] if c else "") or "",
            "profile_url": f"https://www.youtube.com/channel/{raw['channel_id']}",
            "bio": raw.get("channel_description", ""),
            "follower_count": raw.get("subscriber_count", 0),
            "post_count": (c["video_count"] if c else 0) or 0,
            "avatar_url": (c["thumbnail_url"] if c else "") or "", "source_status": "connected", "raw": {},
        }

    def fetch_content(self):
        videos = youtube_metadata.fetch_videos(
            self.channel, max_videos=self.max_items,
            force_refresh=self.config.get("force_refresh", False),
            progress=self.progress)
        # legacy deep-pass + optional transcripts (never blocks)
        youtube_metadata.select_deep_pass(self.creator_id, int(self.config.get("deep_pass", 25)))
        tstats = transcript_optional.fetch_transcripts(
            self.creator_id, skip=self.config.get("skip_transcripts", False), progress=self.progress)
        self.transcript_stats = tstats
        return videos

    def normalize_content_item(self, v):
        transcript = transcript_optional.get_transcript(v["video_id"])
        item = {
            "platform_content_id": v["video_id"],
            "canonical_url": v["video_url"],
            "content_type": "video",
            "title": v["title"], "description": v["description"],
            "text_body": transcript or "",
            "published_at": v["published_at"],
            "duration_seconds": v["duration_seconds"],
            "thumbnail_url": v["thumbnail_url"],
            "metrics": {"views": v["view_count"], "likes": v["like_count"],
                        "comments": v["comment_count"]},
            "source_type": "real_transcript" if transcript else "public_metadata",
            "confidence": "high" if transcript else "medium",
            "raw": {},
        }
        if transcript:
            save_media(content_id_for("youtube", v["video_id"]), "video",
                       media_url=v["video_url"], transcript_text=transcript,
                       duration_seconds=v["duration_seconds"],
                       source_type="real_transcript", confidence="high")
        return item

    def fetch_comments(self, normalized_items):
        try:
            comments_fetch.fetch_comments(self.creator_id,
                                          force_refresh=self.config.get("force_refresh", False),
                                          progress=self.progress)
        except Exception as e:
            log.warning("youtube comments: %s", e)
        # mirror legacy comments into the universal model
        out = []
        with get_db() as db:
            rows = db.execute(
                """SELECT c.* FROM comments c JOIN videos v ON v.video_id=c.video_id
                   WHERE v.creator_id=? AND (c.content_id IS NULL OR c.content_id='')""",
                (self.creator_id,)).fetchall()
            for r in rows:
                db.execute("UPDATE comments SET content_id=?, platform='youtube' WHERE comment_id=?",
                           (content_id_for("youtube", r["video_id"]), r["comment_id"]))
        return out
