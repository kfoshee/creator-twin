"""Soft-delete junk creators (e.g. 'p'/'reel' from old Instagram post URL bugs).

Run: python scripts/cleanup_bad_creators.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.db import get_db, now

BAD_NAMES = ("p", "reel", "reels", "stories", "")


def main():
    with get_db() as db:
        rows = db.execute(
            """SELECT c.creator_id,
                      COALESCE(NULLIF(c.channel_title,''), c.display_name, c.primary_handle, '') AS name,
                      (SELECT COUNT(*) FROM content_items ci WHERE ci.creator_id=c.creator_id) AS items
               FROM creators c WHERE c.deleted_at IS NULL""").fetchall()
        removed = 0
        for r in rows:
            name = (r["name"] or "").strip().lstrip("@").lower()
            if (name in BAD_NAMES or name.startswith("cr_")) and r["items"] == 0:
                db.execute("UPDATE creators SET deleted_at=?, deleted_reason='junk_cleanup' WHERE creator_id=?",
                           (now(), r["creator_id"]))
                print(f"  removed: {r['creator_id']} ({r['name'] or 'unnamed'})")
                removed += 1
    print(f"Cleaned {removed} junk creators.")


if __name__ == "__main__":
    main()
