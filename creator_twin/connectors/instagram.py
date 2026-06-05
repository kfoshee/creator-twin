"""Instagram connector.

Modes (no fragile scraping by default):
  A. official_api  — INSTAGRAM_ACCESS_TOKEN for a Business/Creator account (Graph API)
  B. manual_export — creator-uploaded data export (JSON/CSV from Instagram's
     "Download your information"), via --instagram-export PATH

With only a handle and neither mode available, we store a profile stub
(source_type='public_metadata') and skip content — never block the build.
"""
import csv
import json
import logging
from pathlib import Path

import requests

from ..config import INSTAGRAM_ACCESS_TOKEN
from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.instagram")
GRAPH = "https://graph.instagram.com"


class InstagramConnector(BaseConnector):
    platform = "instagram"

    def validate_config(self):
        if not (self.config.get("handle") or self.config.get("export_path")):
            self.errors.append("no instagram handle or export provided")
            return False
        self.mode = ("official_api" if INSTAGRAM_ACCESS_TOKEN
                     else "manual_export" if self.config.get("export_path")
                     else "stub")
        return True

    def fetch_profile(self):
        if self.mode == "official_api":
            r = requests.get(f"{GRAPH}/me", params={
                "fields": "id,username,account_type,media_count",
                "access_token": INSTAGRAM_ACCESS_TOKEN}, timeout=15)
            if r.status_code == 200:
                return {"mode": "official_api", **r.json()}
            self.errors.append(f"graph api: {r.status_code}")
            self.mode = "manual_export" if self.config.get("export_path") else "stub"
        handle = self.config.get("handle", "")
        return {"mode": self.mode, "username": handle}

    def normalize_profile(self, raw):
        return {"platform_user_id": str(raw.get("id", "")), "handle": raw.get("username", ""),
                "profile_url": f"https://instagram.com/{raw.get('username', '')}",
                "post_count": raw.get("media_count", 0),
                "source_status": "connected" if raw.get("mode") == "official_api" or self.config.get("export_path") else "needs_auth",
                "raw": raw}

    def fetch_content(self):
        if self.mode == "official_api":
            r = requests.get(f"{GRAPH}/me/media", params={
                "fields": "id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,like_count,comments_count",
                "limit": min(self.max_items or 100, 100), "access_token": INSTAGRAM_ACCESS_TOKEN}, timeout=20)
            if r.status_code == 200:
                return [{"_src": "official_api", **m} for m in r.json().get("data", [])]
            self.errors.append(f"graph media: {r.status_code}")
        if self.config.get("export_path"):
            return self._load_export(self.config["export_path"])
        seed = self.config.get("seed_post")
        if seed:  # a pasted post URL that resolved — ingest it as a single seed item
            return [{"_src": "seed_post",
                     "id": seed.get("shortcode") or "seed",
                     "caption": seed.get("caption", ""),
                     "permalink": seed.get("canonical_url", ""),
                     "timestamp": "",
                     "media_type": "VIDEO" if seed.get("media_type") == "reel" else "IMAGE",
                     "media_url": seed.get("image_url", ""),
                     "thumbnail_url": seed.get("image_url", "")}]
        if self.mode == "stub":
            self.errors.append("instagram: handle stored; add INSTAGRAM_ACCESS_TOKEN or an export to ingest posts")
        return []

    def _load_export(self, path):
        """Parse Instagram data-export JSON (posts_1.json style) or a CSV."""
        p = Path(path)
        items = []
        files = [p] if p.is_file() else list(p.rglob("*.json")) + list(p.rglob("*.csv"))
        for f in files:
            try:
                if f.suffix == ".json":
                    data = json.loads(f.read_text(errors="ignore"))
                    posts = data if isinstance(data, list) else data.get("ig_posts") or \
                        data.get("media") or data.get("posts") or []
                    for post in posts:
                        media = post.get("media", [post]) if isinstance(post, dict) else []
                        first = media[0] if media else {}
                        items.append({"_src": "manual_export",
                                      "id": str(first.get("uri", "") or first.get("creation_timestamp", len(items))),
                                      "caption": (post.get("title") or first.get("title") or "")[:5000],
                                      "timestamp": str(first.get("creation_timestamp", "")),
                                      "media_type": "IMAGE", "permalink": ""})
                elif f.suffix == ".csv":
                    for row in csv.DictReader(f.read_text(errors="ignore").splitlines()):
                        row = {k.lower().strip(): v for k, v in row.items() if k}
                        items.append({"_src": "manual_export",
                                      "id": row.get("id") or row.get("url") or str(len(items)),
                                      "caption": row.get("caption") or row.get("description", ""),
                                      "permalink": row.get("url") or row.get("permalink", ""),
                                      "timestamp": row.get("date") or row.get("timestamp", ""),
                                      "media_type": row.get("type", "IMAGE").upper(),
                                      "like_count": row.get("likes"), "comments_count": row.get("comments")})
            except Exception as e:
                self.errors.append(f"export parse {f.name}: {e}")
        return items

    def normalize_content_item(self, m):
        if not (m.get("caption") or m.get("permalink")):
            return None
        ctype = {"VIDEO": "reel", "CAROUSEL_ALBUM": "carousel"}.get(m.get("media_type", ""), "post")
        caption = m.get("caption") or ""
        hashtags = [w[1:] for w in caption.split() if w.startswith("#")]
        return {
            "platform_content_id": str(m.get("id", "")),
            "canonical_url": m.get("permalink", ""),
            "content_type": ctype, "caption": caption, "text_body": caption,
            "published_at": str(m.get("timestamp", "")),
            "thumbnail_url": m.get("thumbnail_url") or m.get("media_url", ""),
            "media_urls": [u for u in [m.get("media_url")] if u],
            "hashtags": hashtags[:30],
            "metrics": {k: m[k] for k in ("like_count", "comments_count") if m.get(k)},
            "source_type": "official_api" if m.get("_src") == "official_api" else "manual_export",
            "confidence": "high",
            "raw": {},
        }
