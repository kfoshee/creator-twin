"""Threads connector — manual export / URL-list mode first.

Accepts a JSON export or a text/CSV list of posts. No scraping by default.
"""
import csv
import json
import logging
from pathlib import Path

from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.threads")


class ThreadsConnector(BaseConnector):
    platform = "threads"

    def validate_config(self):
        if not (self.config.get("handle") or self.config.get("export_path")):
            self.errors.append("no threads handle or export provided")
            return False
        return True

    def fetch_profile(self):
        return {"username": (self.config.get("handle") or "").lstrip("@")}

    def normalize_profile(self, raw):
        h = raw.get("username", "")
        return {"handle": h, "profile_url": f"https://www.threads.net/@{h}" if h else "",
                "source_status": "connected" if self.config.get("export_path") else "needs_upload", "raw": raw}

    def fetch_content(self):
        path = self.config.get("export_path")
        if not path:
            self.errors.append("threads: handle stored; upload an export to ingest posts")
            return []
        p = Path(path)
        items = []
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.suffix in (".json", ".csv", ".txt")]
        for f in files:
            try:
                if f.suffix == ".json":
                    data = json.loads(f.read_text(errors="ignore"))
                    posts = data if isinstance(data, list) else \
                        data.get("text_post_app_text_posts", []) or data.get("posts", [])
                    for post in posts:
                        media = post.get("media", [post]) if isinstance(post, dict) else []
                        first = media[0] if media else {}
                        items.append({"id": str(first.get("creation_timestamp", len(items))),
                                      "text": post.get("title") or first.get("title", ""),
                                      "date": str(first.get("creation_timestamp", "")), "url": ""})
                elif f.suffix == ".csv":
                    for row in csv.DictReader(f.read_text(errors="ignore").splitlines()):
                        row = {k.lower().strip(): v for k, v in row.items() if k}
                        items.append({"id": row.get("id") or str(len(items)),
                                      "text": row.get("text") or row.get("post", ""),
                                      "date": row.get("date", ""), "url": row.get("url", "")})
                elif f.suffix == ".txt":
                    for i, block in enumerate(f.read_text(errors="ignore").split("\n\n")):
                        if block.strip():
                            items.append({"id": f"{f.stem}_{i}", "text": block.strip(),
                                          "date": "", "url": ""})
            except Exception as e:
                self.errors.append(f"export parse {f.name}: {e}")
        return items

    def normalize_content_item(self, post):
        text = post.get("text", "")
        if not text:
            return None
        return {
            "platform_content_id": str(post["id"]),
            "canonical_url": post.get("url", ""),
            "content_type": "post",
            "text_body": text, "caption": text[:500],
            "published_at": post.get("date", ""),
            "hashtags": [w[1:] for w in text.split() if w.startswith("#")][:30],
            "source_type": "manual_export",
            "confidence": "high",
            "raw": {},
        }
