"""Website/blog connector — small, polite same-domain crawl.

Default 25 pages max, respects robots.txt, prioritizes about/blog/product/faq
pages. source_type='website_crawl'.
"""
import html as html_mod
import logging
import re
import urllib.parse
import urllib.robotparser

import requests

from ..config import WEBSITE_MAX_PAGES
from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.website")
UA = "CreatorTwinBot/1.0 (+creator-authorized indexing)"
PRIORITY = ("about", "blog", "product", "course", "faq", "newsletter", "resource", "shop", "link")


def _clean_html(page: str) -> str:
    page = re.sub(r"<(script|style|nav|footer|svg)[^>]*>.*?</\1>", " ", page, flags=re.DOTALL | re.IGNORECASE)
    page = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html_mod.unescape(page)).strip()


class WebsiteConnector(BaseConnector):
    platform = "website"

    def validate_config(self):
        if not self.config.get("website_url"):
            self.errors.append("no website_url provided")
            return False
        return True

    def fetch_profile(self):
        return {"url": self.config["website_url"]}

    def normalize_profile(self, raw):
        return {"handle": urllib.parse.urlparse(raw["url"]).netloc,
                "profile_url": raw["url"], "source_status": "connected", "raw": raw}

    def fetch_content(self):
        start = self.config["website_url"]
        if "://" not in start:
            start = "https://" + start
        base = urllib.parse.urlparse(start)
        max_pages = min(int(self.config.get("max_pages", WEBSITE_MAX_PAGES)), 50)

        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.set_url(f"{base.scheme}://{base.netloc}/robots.txt")
            rp.read()
        except Exception:
            rp = None

        seen, queue, pages = set(), [start], []
        while queue and len(pages) < max_pages:
            url = queue.pop(0)
            norm = url.split("#")[0].rstrip("/")
            if norm in seen:
                continue
            seen.add(norm)
            if rp and not rp.can_fetch(UA, url):
                continue
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=12)
                if "text/html" not in resp.headers.get("content-type", ""):
                    continue
                page = resp.text[:500_000]
            except Exception as e:
                log.debug("skip %s: %s", url, e)
                continue
            m = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
            title = html_mod.unescape(m.group(1)).strip()[:200] if m else url
            headings = re.findall(r"<h[12][^>]*>(.*?)</h[12]>", page, re.IGNORECASE | re.DOTALL)
            text = _clean_html(page)
            if len(text) > 200:
                pages.append({"url": norm, "title": title, "text": text[:30000],
                              "headings": [_clean_html(h)[:120] for h in headings[:10]]})
                self.progress(f"website: crawled {len(pages)}/{max_pages} pages")
            # discover same-domain links, priority pages first
            links = re.findall(r'href=["\']([^"\'#]+)', page)
            found = []
            for link in links:
                u = urllib.parse.urljoin(url, link)
                p = urllib.parse.urlparse(u)
                if p.netloc == base.netloc and p.scheme in ("http", "https") and \
                   not re.search(r"\.(jpg|png|gif|pdf|zip|css|js|ico|svg|mp4|webp)$", p.path, re.I):
                    found.append(u)
            found.sort(key=lambda u: 0 if any(k in u.lower() for k in PRIORITY) else 1)
            queue.extend(u for u in found if u.split("#")[0].rstrip("/") not in seen)
        return pages

    def normalize_content_item(self, page):
        return {
            "platform_content_id": re.sub(r"\W+", "_", page["url"])[:120],
            "canonical_url": page["url"],
            "content_type": "web_page",
            "title": page["title"],
            "text_body": page["text"],
            "raw": {"headings": page["headings"]},
            "source_type": "website_crawl",
            "confidence": "high",
        }
