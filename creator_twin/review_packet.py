"""Creator Review Layer: generate creator_review_packet.md for approval."""
import json
import logging

from .config import PROFILE_DIR
from .creator_fingerprint import get_fingerprint
from .db import get_db
from .llm import complete
from .prompts import REVIEW_PACKET_PROMPT

log = logging.getLogger("creator_twin.review")


def generate_review_packet(creator_id: str) -> str:
    profile = get_fingerprint(creator_id)
    if not profile:
        raise RuntimeError("No fingerprint found — run fast_build.py first")
    with get_db() as db:
        creator = db.execute("SELECT channel_title FROM creators WHERE creator_id=?", (creator_id,)).fetchone()
        stats_row = db.execute(
            """SELECT COUNT(*) AS videos,
               SUM(CASE WHEN transcript_status='fetched' THEN 1 ELSE 0 END) AS transcripts
               FROM videos WHERE creator_id=?""", (creator_id,)).fetchone()
    stats = f"{stats_row['videos']} videos, {stats_row['transcripts'] or 0} real transcripts"
    prompt = REVIEW_PACKET_PROMPT.format(
        fingerprint=json.dumps({k: v for k, v in profile.items() if k != "_meta"}, indent=1),
        stats=stats, creator_name=creator["channel_title"], creator_id=creator_id)
    md = complete(prompt, max_tokens=6000, temperature=0.4)
    out = PROFILE_DIR / f"{creator_id}_creator_review_packet.md"
    out.write_text(md)
    log.info("Review packet written to %s", out)
    return str(out)
