"""Persist normalized creator profile records (one per platform)."""
import json

from ..db import get_db, new_id, now


def save_profile(creator_id: str, platform: str, p: dict) -> str:
    """Upsert a creator_profiles row. `p` keys mirror the table columns."""
    with get_db() as db:
        existing = db.execute(
            "SELECT profile_id FROM creator_profiles WHERE creator_id=? AND platform=?",
            (creator_id, platform)).fetchone()
        profile_id = existing["profile_id"] if existing else new_id("pf")
        row = {
            "profile_id": profile_id, "creator_id": creator_id, "platform": platform,
            "platform_user_id": str(p.get("platform_user_id", "")),
            "handle": p.get("handle", ""), "profile_url": p.get("profile_url", ""),
            "bio": p.get("bio", ""), "follower_count": int(p.get("follower_count", 0) or 0),
            "following_count": int(p.get("following_count", 0) or 0),
            "post_count": int(p.get("post_count", 0) or 0),
            "avatar_url": p.get("avatar_url", ""), "verified_status": str(p.get("verified_status", "")),
            "source_status": p.get("source_status", "found"),
            "raw_json": json.dumps(p.get("raw", {}))[:20000], "fetched_at": now(), "updated_at": now(),
        }
        if existing:
            cols = [c for c in row if c != "profile_id"]
            db.execute(f"UPDATE creator_profiles SET {', '.join(f'{c}=?' for c in cols)} WHERE profile_id=?",
                       [row[c] for c in cols] + [profile_id])
        else:
            row["created_at"] = now()
            db.execute(f"INSERT INTO creator_profiles ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       list(row.values()))
        return profile_id
