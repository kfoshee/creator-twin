"""YouTube Data API v3 metadata fetching with on-disk caching.

Fast by design: channel + playlists + up to N videos of metadata only.
No transcripts here — those are optional enrichment (transcript_optional.py).
"""
import hashlib
import json
import logging
import re
import time
import urllib.parse

import requests

from .config import CACHE_DIR, YOUTUBE_API_KEY
from .db import get_db, new_id, now, upsert

log = logging.getLogger("creator_twin.youtube")
API = "https://www.googleapis.com/youtube/v3"


class YouTubeError(Exception):
    pass


def _cache_path(endpoint: str, params: dict):
    key = hashlib.sha1(f"{endpoint}|{json.dumps(params, sort_keys=True)}".encode()).hexdigest()
    return CACHE_DIR / f"yt_{key}.json"


def api_get(endpoint: str, params: dict, force_refresh: bool = False) -> dict:
    """GET a YouTube API endpoint with caching."""
    if not YOUTUBE_API_KEY:
        raise YouTubeError("YOUTUBE_API_KEY is not set. Add it to .env")
    cache_file = _cache_path(endpoint, params)
    if cache_file.exists() and not force_refresh:
        return json.loads(cache_file.read_text())
    full = dict(params, key=YOUTUBE_API_KEY)
    for attempt in range(3):
        resp = requests.get(f"{API}/{endpoint}", params=full, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            cache_file.write_text(json.dumps(data))
            return data
        if resp.status_code in (403, 429) and "quota" in resp.text.lower():
            raise YouTubeError("YouTube API quota exceeded")
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise YouTubeError(f"YouTube API error {resp.status_code}: {resp.text[:300]}")


def resolve_channel_id(channel_url_or_id: str, force_refresh=False) -> str:
    """Resolve any channel URL / @handle / id form to a UC... channel_id."""
    s = channel_url_or_id.strip()
    if re.fullmatch(r"UC[\w-]{22}", s):
        return s
    parsed = urllib.parse.urlparse(s if "://" in s else f"https://{s}")
    path = parsed.path.strip("/")

    m = re.match(r"channel/(UC[\w-]{22})", path)
    if m:
        return m.group(1)

    handle = None
    if path.startswith("@"):
        handle = path.split("/")[0]
    elif s.startswith("@"):
        handle = s.split("/")[0]
    if handle:
        data = api_get("channels", {"part": "id", "forHandle": handle}, force_refresh)
        items = data.get("items", [])
        if items:
            return items[0]["id"]

    m = re.match(r"user/([\w.-]+)", path)
    if m:
        data = api_get("channels", {"part": "id", "forUsername": m.group(1)}, force_refresh)
        items = data.get("items", [])
        if items:
            return items[0]["id"]

    # /c/Name, bare name, or anything else -> search
    query = path.split("/")[-1] if path else s
    data = api_get("search", {"part": "snippet", "type": "channel", "q": query, "maxResults": 1}, force_refresh)
    items = data.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]
    raise YouTubeError(f"Could not resolve channel from: {channel_url_or_id}")


def parse_duration(iso: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def fetch_channel(channel_id: str, channel_url: str = "", force_refresh=False) -> dict:
    """Fetch channel metadata and upsert the creator row. Returns creator dict."""
    data = api_get("channels", {
        "part": "snippet,statistics,contentDetails,brandingSettings", "id": channel_id
    }, force_refresh)
    items = data.get("items", [])
    if not items:
        raise YouTubeError(f"Channel not found: {channel_id}")
    ch = items[0]
    sn, st = ch["snippet"], ch.get("statistics", {})
    branding = ch.get("brandingSettings", {}).get("channel", {})
    description = sn.get("description") or branding.get("description") or ""

    with get_db() as db:
        existing = db.execute("SELECT creator_id FROM creators WHERE channel_id=?", (channel_id,)).fetchone()
        creator_id = existing["creator_id"] if existing else new_id("cr")
        upsert(db, "creators", "channel_id", {
            "creator_id": creator_id,
            "channel_id": channel_id,
            "channel_url": channel_url or f"https://www.youtube.com/channel/{channel_id}",
            "channel_title": sn.get("title", ""),
            "channel_description": description,
            "custom_url": sn.get("customUrl", ""),
            "subscriber_count": int(st.get("subscriberCount", 0) or 0),
            "video_count": int(st.get("videoCount", 0) or 0),
            "view_count": int(st.get("viewCount", 0) or 0),
            "thumbnail_url": (sn.get("thumbnails", {}).get("high") or sn.get("thumbnails", {}).get("default") or {}).get("url", ""),
        })
    uploads_playlist = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    return {
        "creator_id": creator_id, "channel_id": channel_id,
        "channel_title": sn.get("title", ""), "channel_description": description,
        "uploads_playlist": uploads_playlist,
        "subscriber_count": int(st.get("subscriberCount", 0) or 0),
        "keywords": branding.get("keywords", ""),
    }


def fetch_playlists(channel_id: str, force_refresh=False, max_playlists=25) -> list:
    playlists, page = [], None
    while len(playlists) < max_playlists:
        params = {"part": "snippet,contentDetails", "channelId": channel_id, "maxResults": 50}
        if page:
            params["pageToken"] = page
        data = api_get("playlists", params, force_refresh)
        for p in data.get("items", []):
            playlists.append({
                "playlist_id": p["id"],
                "title": p["snippet"].get("title", ""),
                "description": p["snippet"].get("description", ""),
                "item_count": p.get("contentDetails", {}).get("itemCount", 0),
            })
        page = data.get("nextPageToken")
        if not page:
            break
    return playlists[:max_playlists]


def fetch_playlist_video_ids(playlist_id: str, limit=50, force_refresh=False, progress=None) -> list:
    """Paginate through a playlist. limit=None fetches the FULL catalog
    (keeps following nextPageToken until exhausted)."""
    ids, page = [], None
    while limit is None or len(ids) < limit:
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
        if page:
            params["pageToken"] = page
        try:
            data = api_get("playlistItems", params, force_refresh)
        except YouTubeError:
            break
        ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
        page = data.get("nextPageToken")
        if progress:
            progress(f"Catalog scan: {len(ids)} videos found so far")
        if not page:
            break
    return ids if limit is None else ids[:limit]


def fetch_videos(creator: dict, max_videos=200, force_refresh=False, progress=None) -> list:
    """Fetch full video metadata via uploads playlist + videos.list.
    max_videos=None fetches the entire channel catalog (full pagination)."""
    creator_id = creator["creator_id"]
    video_ids = fetch_playlist_video_ids(creator["uploads_playlist"], limit=max_videos,
                                         force_refresh=force_refresh, progress=progress)
    log.info("Found %d videos in uploads playlist", len(video_ids))
    if not video_ids:
        # some channels 404 their uploads playlist — fall back to search.list
        log.info("uploads playlist empty/404 — falling back to search API")
        page, cap = None, max_videos or 200
        while len(video_ids) < cap:
            params = {"part": "id", "channelId": creator["channel_id"], "type": "video",
                      "order": "date", "maxResults": 50}
            if page:
                params["pageToken"] = page
            try:
                data = api_get("search", params, force_refresh)
            except YouTubeError:
                break
            video_ids += [i["id"]["videoId"] for i in data.get("items", [])
                          if i.get("id", {}).get("videoId")]
            page = data.get("nextPageToken")
            if not page:
                break
        video_ids = video_ids[:cap]
        log.info("search fallback found %d videos", len(video_ids))

    # map videos -> playlists (top playlists only, cheap)
    playlists = fetch_playlists(creator["channel_id"], force_refresh)
    video_playlists = {}
    for p in sorted(playlists, key=lambda x: -(x["item_count"] or 0))[:8]:
        for vid in fetch_playlist_video_ids(p["playlist_id"], limit=50, force_refresh=force_refresh):
            video_playlists.setdefault(vid, []).append(p["playlist_id"])

    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = api_get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)}, force_refresh)
        with get_db() as db:
            for v in data.get("items", []):
                sn, st = v["snippet"], v.get("statistics", {})
                row = {
                    "video_id": v["id"],
                    "creator_id": creator_id,
                    "title": sn.get("title", ""),
                    "description": sn.get("description", ""),
                    "published_at": sn.get("publishedAt", ""),
                    "duration_seconds": parse_duration(v.get("contentDetails", {}).get("duration", "")),
                    "view_count": int(st.get("viewCount", 0) or 0),
                    "like_count": int(st.get("likeCount", 0) or 0),
                    "comment_count": int(st.get("commentCount", 0) or 0),
                    "playlist_ids": json.dumps(video_playlists.get(v["id"], [])),
                    "thumbnail_url": (sn.get("thumbnails", {}).get("medium") or {}).get("url", ""),
                    "video_url": f"https://www.youtube.com/watch?v={v['id']}",
                    "metadata_json": json.dumps({"tags": sn.get("tags", [])[:20], "categoryId": sn.get("categoryId", "")}),
                }
                upsert(db, "videos", "video_id", row)
                videos.append(row)
        if progress:
            progress(f"Fetched metadata for {len(videos)}/{len(video_ids)} videos")
    return videos


def select_deep_pass(creator_id: str, deep_pass_n=40) -> list:
    """Score and select high-signal videos for the deep pass."""
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM videos WHERE creator_id=?", (creator_id,)).fetchall()]
        if not rows:
            return []
        max_views = max(r["view_count"] or 0 for r in rows) or 1
        max_comments = max(r["comment_count"] or 0 for r in rows) or 1
        rows_sorted = sorted(rows, key=lambda r: r["published_at"] or "", reverse=True)
        recency_rank = {r["video_id"]: i for i, r in enumerate(rows_sorted)}

        # central topic terms from titles
        words = {}
        for r in rows:
            for w in re.findall(r"[a-z]{4,}", (r["title"] or "").lower()):
                words[w] = words.get(w, 0) + 1
        top_terms = {w for w, c in sorted(words.items(), key=lambda x: -x[1])[:25] if c >= 3}

        scored = []
        for r in rows:
            views = (r["view_count"] or 0) / max_views
            comments = (r["comment_count"] or 0) / max_comments
            recency = 1 - recency_rank[r["video_id"]] / max(len(rows) - 1, 1)
            rich_desc = min(len(r["description"] or "") / 800, 1.0)
            in_playlist = 0.5 if json.loads(r["playlist_ids"] or "[]") else 0
            title_words = set(re.findall(r"[a-z]{4,}", (r["title"] or "").lower()))
            centrality = min(len(title_words & top_terms) / 3, 1.0)
            score = views * 2.0 + comments + recency * 1.2 + rich_desc * 0.8 + in_playlist + centrality
            scored.append((score, r))

        scored.sort(key=lambda x: -x[0])
        selected = [r["video_id"] for _, r in scored[:deep_pass_n]]
        db.execute("UPDATE videos SET selected_for_deep_pass=0 WHERE creator_id=?", (creator_id,))
        db.executemany("UPDATE videos SET selected_for_deep_pass=1 WHERE video_id=?",
                       [(v,) for v in selected])
        return selected
