"""Persist normalized content_items and comments from any platform."""
import json

from ..db import get_db, new_id, now


def content_id_for(platform: str, platform_content_id: str) -> str:
    return f"{platform}:{platform_content_id}"


def save_content_item(creator_id: str, platform: str, item: dict) -> str:
    """Upsert a content_items row. `item` keys mirror table columns (json fields as objects)."""
    cid = content_id_for(platform, str(item["platform_content_id"]))
    row = {
        "content_id": cid, "creator_id": creator_id, "platform": platform,
        "platform_content_id": str(item["platform_content_id"]),
        "canonical_url": item.get("canonical_url", ""),
        "content_type": item.get("content_type", "post"),
        "title": (item.get("title") or "")[:500],
        "caption": (item.get("caption") or "")[:5000],
        "description": (item.get("description") or "")[:10000],
        "text_body": (item.get("text_body") or "")[:200000],
        "published_at": item.get("published_at", ""),
        "duration_seconds": int(item.get("duration_seconds", 0) or 0),
        "thumbnail_url": item.get("thumbnail_url", ""),
        "media_urls_json": json.dumps(item.get("media_urls", [])),
        "hashtags_json": json.dumps(item.get("hashtags", [])),
        "mentions_json": json.dumps(item.get("mentions", [])),
        "metrics_json": json.dumps(item.get("metrics", {})),
        "raw_json": json.dumps(item.get("raw", {}))[:20000],
        "source_type": item.get("source_type", "public_metadata"),
        "confidence": item.get("confidence", "medium"),
        "fetched_at": now(), "updated_at": now(),
    }
    with get_db() as db:
        exists = db.execute("SELECT 1 FROM content_items WHERE content_id=?", (cid,)).fetchone()
        if exists:
            cols = [c for c in row if c != "content_id"]
            db.execute(f"UPDATE content_items SET {', '.join(f'{c}=?' for c in cols)} WHERE content_id=?",
                       [row[c] for c in cols] + [cid])
        else:
            row["created_at"] = now()
            db.execute(f"INSERT INTO content_items ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       list(row.values()))
    return cid


def save_comment(content_id: str, platform: str, c: dict):
    with get_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO comments
               (comment_id, video_id, content_id, platform, author_name, author_handle, text,
                like_count, reply_count, published_at, is_top_comment, raw_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.get("comment_id") or new_id("cm"), c.get("video_id", ""), content_id, platform,
             c.get("author_name", ""), c.get("author_handle", ""), (c.get("text") or "")[:2000],
             int(c.get("like_count", 0) or 0), int(c.get("reply_count", 0) or 0),
             c.get("published_at", ""), 1 if c.get("is_top_comment", True) else 0,
             json.dumps(c.get("raw", {}))[:5000], now()))
