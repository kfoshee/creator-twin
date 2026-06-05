"""X/Twitter connector.

Modes: official API (X_BEARER_TOKEN) or uploaded archive/export
(tweets.js / JSON / CSV). Threads are preserved via conversation grouping.
"""
import csv
import json
import logging
import re
from pathlib import Path

import requests

from ..config import X_BEARER_TOKEN
from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.x")
API = "https://api.twitter.com/2"


class XConnector(BaseConnector):
    platform = "x"

    def validate_config(self):
        if not (self.config.get("handle") or self.config.get("export_path")):
            self.errors.append("no x handle or archive provided")
            return False
        return True

    def _api(self, path, params):
        r = requests.get(f"{API}/{path}", params=params,
                         headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"x api {r.status_code}: {r.text[:150]}")
        return r.json()

    def fetch_profile(self):
        handle = (self.config.get("handle") or "").lstrip("@")
        if X_BEARER_TOKEN and handle:
            try:
                data = self._api(f"users/by/username/{handle}",
                                 {"user.fields": "description,public_metrics,verified,profile_image_url"})
                self.user = data.get("data", {})
                return {"mode": "official_api", **self.user}
            except Exception as e:
                self.errors.append(str(e))
        return {"mode": "manual", "username": handle}

    def normalize_profile(self, raw):
        pm = raw.get("public_metrics", {})
        return {"platform_user_id": str(raw.get("id", "")), "handle": raw.get("username", ""),
                "profile_url": f"https://x.com/{raw.get('username', '')}",
                "bio": raw.get("description", ""),
                "follower_count": pm.get("followers_count", 0),
                "following_count": pm.get("following_count", 0),
                "post_count": pm.get("tweet_count", 0),
                "verified_status": str(raw.get("verified", "")),
                "source_status": "connected" if raw.get("mode") == "official_api" or self.config.get("export_path") else "needs_auth",
                "raw": raw}

    def fetch_content(self):
        if X_BEARER_TOKEN and getattr(self, "user", {}).get("id"):
            try:
                data = self._api(f"users/{self.user['id']}/tweets", {
                    "max_results": min(self.max_items or 100, 100),
                    "tweet.fields": "created_at,public_metrics,conversation_id,entities",
                    "exclude": "retweets,replies"})
                return [{"_src": "official_api", **t} for t in data.get("data", [])]
            except Exception as e:
                self.errors.append(str(e))
        if self.config.get("export_path"):
            return self._load_archive(self.config["export_path"])
        self.errors.append("x: handle stored; add X_BEARER_TOKEN or an archive to ingest posts")
        return []

    def _load_archive(self, path):
        p = Path(path)
        items = []
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.name in ("tweets.js", "tweet.js")
                                         or f.suffix in (".json", ".csv")]
        for f in files:
            try:
                raw = f.read_text(errors="ignore")
                if f.suffix == ".js":  # archive format: window.YTD.tweets.part0 = [...]
                    raw = re.sub(r"^window\.YTD\.\w+\.part\d+\s*=\s*", "", raw.strip())
                if f.suffix in (".js", ".json"):
                    data = json.loads(raw)
                    for entry in (data if isinstance(data, list) else data.get("tweets", [])):
                        t = entry.get("tweet", entry)
                        items.append({"_src": "uploaded_export", "id": t.get("id_str") or t.get("id"),
                                      "text": t.get("full_text") or t.get("text", ""),
                                      "created_at": t.get("created_at", ""),
                                      "conversation_id": t.get("conversation_id", ""),
                                      "public_metrics": {"like_count": int(t.get("favorite_count", 0) or 0),
                                                         "retweet_count": int(t.get("retweet_count", 0) or 0)}})
                elif f.suffix == ".csv":
                    for row in csv.DictReader(raw.splitlines()):
                        row = {k.lower().strip(): v for k, v in row.items() if k}
                        items.append({"_src": "uploaded_export", "id": row.get("id") or str(len(items)),
                                      "text": row.get("text") or row.get("tweet", ""),
                                      "created_at": row.get("date") or row.get("created_at", ""),
                                      "conversation_id": "", "public_metrics": {}})
            except Exception as e:
                self.errors.append(f"archive parse {f.name}: {e}")
        return items

    def normalize_content_item(self, t):
        text = t.get("text", "")
        if not text or text.startswith("RT @"):
            return None
        is_thread = t.get("conversation_id") and str(t.get("conversation_id")) != str(t.get("id"))
        handle = self.config.get("handle", "").lstrip("@")
        return {
            "platform_content_id": str(t["id"]),
            "canonical_url": f"https://x.com/{handle}/status/{t['id']}" if handle else "",
            "content_type": "thread" if is_thread else "post",
            "text_body": text, "caption": text[:500],
            "published_at": t.get("created_at", ""),
            "hashtags": [w[1:] for w in text.split() if w.startswith("#")][:30],
            "mentions": [w[1:] for w in text.split() if w.startswith("@")][:30],
            "metrics": t.get("public_metrics", {}),
            "source_type": "official_api" if t.get("_src") == "official_api" else "uploaded_export",
            "confidence": "high",
            "raw": {"conversation_id": str(t.get("conversation_id", ""))},
        }
