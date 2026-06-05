"""Approve (and optionally override) the creator fingerprint.

Workflow:
1. fast_build.py writes data/profiles/<creator_id>_fingerprint.json and
   <creator_id>_creator_review_packet.md
2. Creator edits the fingerprint JSON (and/or creator_intake.json) freely.
3. Run: python creator_twin/approve_creator_profile.py --creator-id CR_ID
   -> stores the (edited) profile with approved_by_creator=true.
   Approved fields override inferred ones everywhere (chat, index).
4. Re-run build_index.py to refresh chunk confidence.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.config import PROFILE_DIR
from creator_twin.db import get_db, now
from creator_twin.rag.chunker import build_chunks as build_index


def approve(creator_id: str, profile_path=None, reindex=True):
    path = Path(profile_path) if profile_path else PROFILE_DIR / f"{creator_id}_fingerprint.json"
    if not path.exists():
        raise SystemExit(f"Profile file not found: {path}")
    profile = json.loads(path.read_text())
    profile.pop("_meta", None)
    with get_db() as db:
        db.execute(
            "INSERT INTO creator_fingerprint (creator_id, source_type, confidence, profile_json, approved_by_creator, created_at, updated_at)"
            " VALUES (?,?,?,?,1,?,?)",
            (creator_id, "creator_approved", "high", json.dumps(profile), now(), now()))
        # approved profile also blesses synthetic content review status at profile level
        db.execute("UPDATE creator_fingerprint SET approved_by_creator=1 WHERE creator_id=? AND source_type='creator_approved'",
                   (creator_id,))
    print(f"✓ Approved profile stored for {creator_id} (source_type=creator_approved, confidence=high)")
    if reindex:
        n = build_index(creator_id)
        print(f"✓ Index rebuilt: {n} chunks now use the approved profile")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--creator-id", required=True)
    ap.add_argument("--profile", help="Path to edited fingerprint JSON (defaults to data/profiles/<id>_fingerprint.json)")
    ap.add_argument("--no-reindex", action="store_true")
    args = ap.parse_args()
    approve(args.creator_id, args.profile, reindex=not args.no_reindex)
