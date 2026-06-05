"""Export a static demo dataset from the local DB for the GitHub Pages demo.

Usage: python scripts/export_demo_data.py [--creator-id CR_ID]
Writes web/demo/creator_demo.json (no secrets, public metadata only).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.config import ROOT
from creator_twin.creator_fingerprint import get_fingerprint
from creator_twin.db import get_db

CANNED = [
    {"match": "amazon", "reply": {
        "answer": "My take: I'd be cautious here. The specs look fine on paper, but at this price I'd "
                  "want to see real accuracy testing against a reference device before trusting the claims. "
                  "Verdict: consider it only if it drops in price — otherwise I'd wait for independent reviews.",
        "products": [{"title": "Demo Product", "price": "99.99", "url": "#", "image": ""}],
        "sources": [{"platform": "youtube", "title": "Demo video", "source_type": "real_transcript",
                     "confidence": "high", "chunk_type": "real_text_chunk", "url": "", "excerpt": ""}]}},
    {"match": "", "reply": {
        "answer": "This is the static demo — answers are canned. Deploy the backend to get real takes "
                  "(see README_DEPLOY.md). My take on the interface though: works great.",
        "products": [], "sources": []}},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-id")
    args = ap.parse_args()

    with get_db() as db:
        creators = [dict(r) for r in db.execute(
            """SELECT c.creator_id, COALESCE(NULLIF(c.channel_title,''), c.display_name, c.creator_id) AS channel_title,
               c.subscriber_count, c.custom_url, c.thumbnail_url,
               (SELECT COUNT(*) FROM content_items ci WHERE ci.creator_id=c.creator_id) AS videos_in_db,
               (SELECT COUNT(*) FROM rag_chunks r WHERE r.creator_id=c.creator_id) AS chunks
               FROM creators c""").fetchall()]
        if not creators:
            raise SystemExit("No creators in DB — run a fast build first")
        cid = args.creator_id or creators[0]["creator_id"]
        creator = dict(db.execute("SELECT * FROM creators WHERE creator_id=?", (cid,)).fetchone())
        stats = dict(db.execute(
            """SELECT
               (SELECT COUNT(*) FROM content_items WHERE creator_id=?) AS videos,
               (SELECT COUNT(*) FROM content_items WHERE creator_id=? AND source_type='real_transcript') AS transcripts,
               (SELECT COUNT(*) FROM rag_chunks WHERE creator_id=?) AS chunks,
               (SELECT COUNT(*) FROM qa_pairs WHERE creator_id=?) AS qa_pairs""",
            (cid,) * 4).fetchone())

    profile = get_fingerprint(cid) or {}
    with get_db() as db:
        suggested = [dict(r) for r in db.execute(
            """SELECT product_name, product_brand, product_category, product_url, image_url,
                      price_text, reason_label, reason_detail, source_title, source_url,
                      suggestion_type, confidence, final_score
               FROM suggested_products WHERE creator_id=? ORDER BY final_score DESC LIMIT 8""",
            (cid,)).fetchall()]
    demo = {
        "creators": creators,
        "detail": {"creator": creator, "stats": stats, "profile": profile,
                   "videos": [], "review_packet": None},
        "suggested": suggested,
        "canned": CANNED,
    }
    out = ROOT / "web" / "demo" / "creator_demo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(demo, indent=1, default=str))
    print(f"Demo data written to {out}")


if __name__ == "__main__":
    main()
