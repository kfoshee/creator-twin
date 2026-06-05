"""Single Instagram post/reel resolver — best-effort, never crashes the app.

Instagram aggressively blocks anonymous scraping; when blocked we return a
structured needs_manual_profile result so the UI can ask for the profile URL.
"""
import html as html_mod
import logging
import re

import requests

from ..source_detection import classify_instagram_url

log = logging.getLogger("creator_twin.connectors.instagram_post")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}

# og:title forms: "Name (@handle) on Instagram: ..." / "Name on Instagram: ..."
_HANDLE_RE = re.compile(r"\(@([A-Za-z0-9._]{2,30})\)")
_HANDLE_RE2 = re.compile(r'"username"\s*:\s*"([A-Za-z0-9._]{2,30})"')


def resolve_instagram_post(url: str) -> dict:
    info = classify_instagram_url(url)
    shortcode = info.get("shortcode")
    base = {"shortcode": shortcode, "canonical_url": info["canonical_url"],
            "media_type": "reel" if info["input_type"] == "instagram_reel" else "post"}
    try:
        r = requests.get(info["canonical_url"], headers=UA, timeout=10, allow_redirects=True)
        page = r.text[:400_000]
    except Exception as e:
        log.warning("instagram post fetch failed: %s", e)
        return {**base, "status": "needs_manual_profile",
                "message": "Instagram blocked public metadata. Paste the creator profile URL "
                           "or connect/upload Instagram data."}

    def meta(name):
        m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(name), page)
        return html_mod.unescape(m.group(1)) if m else ""

    title = meta("og:title")
    desc = meta("og:description")
    image = meta("og:image")
    handle = None
    m = _HANDLE_RE.search(title) or _HANDLE_RE.search(desc) or _HANDLE_RE2.search(page)
    if m:
        handle = m.group(1)
    if "login" in (r.url or "").lower() and not handle:
        return {**base, "status": "needs_manual_profile",
                "message": "Instagram blocked public metadata. Paste the creator profile URL "
                           "or connect/upload Instagram data."}
    if not handle:
        return {**base, "status": "needs_manual_profile",
                "message": "Couldn't identify the creator from this post. Paste their Instagram "
                           "profile URL to build their twin."}
    display = title.split("(")[0].strip() if "(" in title else handle
    return {**base, "status": "resolved", "handle": handle, "display_name": display,
            "caption": desc[:1500], "image_url": image}
