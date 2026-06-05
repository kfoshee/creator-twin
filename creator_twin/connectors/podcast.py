"""Podcast connector — RSS feed metadata; transcripts only if linked in the feed.

Never downloads/transcribes audio in fast-build mode.
"""
import logging
import re
import xml.etree.ElementTree as ET

import requests

from ..normalization.normalize_media import save_media
from ..normalization.normalize_posts import content_id_for
from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.podcast")
NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
      "podcast": "https://podcastindex.org/namespace/1.0"}


class PodcastConnector(BaseConnector):
    platform = "podcast"

    def validate_config(self):
        if not self.config.get("rss_url"):
            self.errors.append("no podcast rss_url provided")
            return False
        return True

    def fetch_profile(self):
        resp = requests.get(self.config["rss_url"], timeout=15,
                            headers={"User-Agent": "CreatorTwinBot/1.0"})
        self.root = ET.fromstring(resp.content)
        ch = self.root.find("channel")
        return {"title": ch.findtext("title", ""), "description": ch.findtext("description", ""),
                "link": ch.findtext("link", ""), "author": ch.findtext("itunes:author", "", NS)}

    def normalize_profile(self, raw):
        return {"handle": raw.get("title", ""), "profile_url": raw.get("link", ""),
                "bio": re.sub(r"<[^>]+>", " ", raw.get("description", ""))[:2000], "raw": raw}

    def fetch_content(self):
        episodes = []
        for item in self.root.find("channel").findall("item")[: self.max_items]:
            dur = item.findtext("itunes:duration", "", NS)
            secs = 0
            if dur:
                parts = [int(p) for p in dur.split(":") if p.isdigit()]
                secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
            transcript_url = ""
            t = item.find("podcast:transcript", NS)
            if t is not None:
                transcript_url = t.get("url", "")
            episodes.append({
                "guid": item.findtext("guid", "") or item.findtext("link", ""),
                "title": item.findtext("title", ""),
                "description": re.sub(r"<[^>]+>", " ", item.findtext("description", "") or "")[:8000],
                "link": item.findtext("link", ""),
                "pub_date": item.findtext("pubDate", ""),
                "duration": secs,
                "transcript_url": transcript_url,
            })
        return episodes

    def normalize_content_item(self, ep):
        transcript = ""
        if ep["transcript_url"]:
            try:  # only if the feed links one; capped, quick
                resp = requests.get(ep["transcript_url"], timeout=10)
                if resp.status_code == 200:
                    transcript = re.sub(r"\d{2}:\d{2}[:.,\d]*\s*(-->.*)?", "", resp.text)[:200000]
            except Exception:
                pass
        item = {
            "platform_content_id": re.sub(r"\W+", "_", ep["guid"])[:120],
            "canonical_url": ep["link"],
            "content_type": "podcast_episode",
            "title": ep["title"], "description": ep["description"],
            "text_body": transcript,
            "published_at": ep["pub_date"], "duration_seconds": ep["duration"],
            "source_type": "real_transcript" if transcript else "public_metadata",
            "confidence": "high" if transcript else "medium",
            "raw": {},
        }
        if transcript:
            save_media(content_id_for("podcast", item["platform_content_id"]), "audio",
                       media_url=ep["link"], transcript_text=transcript,
                       source_type="real_transcript", confidence="high")
        return item
