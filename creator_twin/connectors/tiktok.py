"""TikTok connector.

Modes: manual export (CSV/JSON of posts, TikTok Shop affiliate exports, or a
plain text file of post URLs) first; placeholder for the official TikTok API
(TIKTOK_ACCESS_TOKEN) when available. Never blocks the build.
"""
import csv
import json
import logging
from pathlib import Path

from ..config import TIKTOK_ACCESS_TOKEN
from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.tiktok")


class TikTokConnector(BaseConnector):
    platform = "tiktok"

    def validate_config(self):
        if not (self.config.get("handle") or self.config.get("export_path")):
            self.errors.append("no tiktok handle or export provided")
            return False
        return True

    def fetch_profile(self):
        return {"username": self.config.get("handle", "")}

    def normalize_profile(self, raw):
        h = raw.get("username", "").lstrip("@")
        return {"handle": h, "profile_url": f"https://www.tiktok.com/@{h}" if h else "",
                "source_status": "connected" if self.config.get("export_path") else "needs_upload", "raw": raw}

    def fetch_content(self):
        if TIKTOK_ACCESS_TOKEN:
            # Placeholder: official TikTok Display API integration point.
            self.errors.append("tiktok official API adapter not yet wired; using export if provided")
        path = self.config.get("export_path")
        if not path:
            self.errors.append("tiktok: handle stored; upload an export or URL list to ingest posts")
            return []
        p = Path(path)
        items = []
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.suffix in (".json", ".csv", ".txt")]
        for f in files:
            try:
                if f.suffix == ".json":
                    data = json.loads(f.read_text(errors="ignore"))
                    videos = data if isinstance(data, list) else \
                        data.get("Video", {}).get("Videos", {}).get("VideoList", []) or \
                        data.get("videos", []) or data.get("posts", [])
                    for v in videos:
                        items.append({"id": str(v.get("id") or v.get("Link") or len(items)),
                                      "caption": v.get("desc") or v.get("Description") or v.get("caption", ""),
                                      "url": v.get("Link") or v.get("url") or v.get("share_url", ""),
                                      "date": str(v.get("Date") or v.get("create_time", "")),
                                      "likes": v.get("Likes") or v.get("like_count")})
                elif f.suffix == ".csv":
                    for row in csv.DictReader(f.read_text(errors="ignore").splitlines()):
                        row = {k.lower().strip(): v for k, v in row.items() if k}
                        items.append({"id": row.get("id") or row.get("url") or str(len(items)),
                                      "caption": row.get("caption") or row.get("description") or row.get("title", ""),
                                      "url": row.get("url") or row.get("link", ""),
                                      "date": row.get("date") or row.get("create time", ""),
                                      "likes": row.get("likes") or row.get("like count")})
                elif f.suffix == ".txt":  # plain list of post URLs
                    for line in f.read_text(errors="ignore").splitlines():
                        line = line.strip()
                        if line.startswith("http"):
                            items.append({"id": line.rstrip("/").split("/")[-1],
                                          "caption": "", "url": line, "date": "", "likes": None})
            except Exception as e:
                self.errors.append(f"export parse {f.name}: {e}")
        return items

    def normalize_content_item(self, v):
        caption = v.get("caption") or ""
        if not (caption or v.get("url")):
            return None
        return {
            "platform_content_id": str(v["id"]),
            "canonical_url": v.get("url", ""),
            "content_type": "short_video",
            "caption": caption, "text_body": caption,
            "published_at": v.get("date", ""),
            "hashtags": [w[1:] for w in caption.split() if w.startswith("#")][:30],
            "metrics": {"likes": v["likes"]} if v.get("likes") else {},
            "source_type": "manual_export" if caption else "uploaded_export",
            "confidence": "high" if caption else "low",
            "raw": {},
        }
