"""Creator source detection: URLs map to their platform; plain handles map to ALL platforms.

A bare @handle must never silently default to YouTube.
"""
import re
import urllib.parse

ALL_PLATFORMS = ["youtube", "instagram", "tiktok", "x", "threads", "website"]

_URL_RULES = [
    (re.compile(r"(youtube\.com|youtu\.be)", re.I), "youtube"),
    (re.compile(r"instagram\.com", re.I), "instagram"),
    (re.compile(r"tiktok\.com", re.I), "tiktok"),
    (re.compile(r"(twitter\.com|(^|//|\.)x\.com)", re.I), "x"),
    (re.compile(r"threads\.net", re.I), "threads"),
]


_IG_NON_PROFILE = {"p", "reel", "reels", "stories", "explore", "tv", "accounts", "direct"}


def classify_instagram_url(url: str) -> dict:
    """Profile vs post vs reel vs story. NEVER returns 'p' or 'reel' as a handle."""
    path = urllib.parse.urlparse(url if "://" in url else "https://" + url).path
    segs = [s for s in path.split("/") if s]
    out = {"platform": "instagram", "input_type": "instagram_profile", "handle": None,
           "shortcode": None, "canonical_url": url.split("?")[0],
           "should_build_creator": False, "needs_profile_resolution": False}
    if not segs:
        return out
    first = segs[0].lower()
    if first in ("p", "reel", "reels", "tv"):
        out.update(input_type="instagram_reel" if first in ("reel", "reels") else "instagram_post",
                   shortcode=segs[1] if len(segs) > 1 else None,
                   needs_profile_resolution=True)
    elif first == "stories":
        out.update(input_type="instagram_story",
                   handle=(segs[1].lstrip("@") if len(segs) > 1 else None),
                   should_build_creator=len(segs) > 1)
    elif first not in _IG_NON_PROFILE:
        out.update(handle=segs[0].lstrip("@"), should_build_creator=True)
    return out


def _handle_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url if "://" in url else "https://" + url).path
    seg = next((s for s in path.split("/") if s and s not in ("channel", "user", "c")), "")
    return seg.lstrip("@")


def detect_creator_source(raw: str) -> dict:
    s = (raw or "").strip()
    is_url = "://" in s or re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", s)
    result = {
        "raw_input": raw,
        "normalized_handle": "",
        "detected_platforms": [],
        "confidence": 0.5,
        "input_type": "unknown",
        "primary_platform": None,
        "should_search_all_platforms": False,
    }
    if not s:
        return result

    if is_url:
        result["input_type"] = "url"
        low = s.lower()
        for rx, platform in _URL_RULES:
            if rx.search(low):
                result.update(primary_platform=platform, detected_platforms=[platform],
                              normalized_handle=_handle_from_url(s), confidence=0.95)
                if platform == "instagram":
                    ig = classify_instagram_url(s)
                    result["instagram"] = ig
                    result["normalized_handle"] = ig["handle"] or ""
                return result
        if low.endswith((".xml", ".rss")) or "rss" in low or "/feed" in low:
            result.update(primary_platform="podcast", detected_platforms=["podcast"], confidence=0.85)
            return result
        result.update(primary_platform="website", detected_platforms=["website"],
                      normalized_handle=urllib.parse.urlparse(
                          s if "://" in s else "https://" + s).netloc, confidence=0.8)
        return result

    # plain @handle or name → search everywhere, assume nothing
    handle = s.lstrip("@").strip()
    result.update(
        input_type="handle" if s.startswith("@") or " " not in s else "name",
        normalized_handle=handle,
        detected_platforms=list(ALL_PLATFORMS),
        primary_platform=None,
        should_search_all_platforms=True,
        confidence=0.6,
    )
    return result


def profile_url_for(platform: str, handle: str) -> str:
    return {
        "youtube": f"https://www.youtube.com/@{handle}",
        "instagram": f"https://instagram.com/{handle}",
        "tiktok": f"https://www.tiktok.com/@{handle}",
        "x": f"https://x.com/{handle}",
        "threads": f"https://www.threads.net/@{handle}",
        "website": f"https://{handle}.com",
    }.get(platform, "")
