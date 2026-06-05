"""Creator Review Layer: cross-platform creator_review_packet.md."""
import json
import logging

from ..config import PROFILE_DIR
from ..creator_fingerprint import get_fingerprint
from ..db import get_db
from ..llm import complete

log = logging.getLogger("creator_twin.review")

PACKET_PROMPT = """Using this cross-platform creator fingerprint and platform summaries, write a \
clear markdown review packet the creator will read and edit. Direct and scannable, no fluff.

FINGERPRINT:
{fingerprint}

PLATFORM SUMMARIES:
{platforms}

STATS: {stats}

Structure (use these exact section headers):
# Creator Review Packet — {name}
## 1. The AI's understanding of you
## 2. Platform-by-platform summary
## 3. Main topics / content pillars
## 4. Audience profile
## 5. Strong opinions & inferred beliefs
## 6. Repeated advice & frameworks
## 7. Tools, products & services mentioned
## 8. Style & tone model
## 9. Things the assistant should NOT say
## 10. Common audience questions
## 11. Facts needing your confirmation
## 12. Suggested assistant behavior
## 13. Source confidence notes

End with: "Edit data/profiles/{creator_id}_fingerprint.json, then run: python creator_twin/review/approve_profile.py --creator-id {creator_id}" """


def generate_review_packet(creator_id: str) -> str:
    profile = get_fingerprint(creator_id)
    if not profile:
        raise RuntimeError("No fingerprint — run fast_build.py first")
    with get_db() as db:
        c = db.execute("SELECT channel_title, display_name FROM creators WHERE creator_id=?",
                       (creator_id,)).fetchone()
        name = (c["display_name"] or c["channel_title"]) if c else creator_id
        platforms = db.execute(
            "SELECT platform, summary_json FROM platform_summaries WHERE creator_id=?",
            (creator_id,)).fetchall()
        stats = db.execute(
            """SELECT COUNT(*) AS items,
               SUM(CASE WHEN text_body != '' THEN 1 ELSE 0 END) AS with_text,
               COUNT(DISTINCT platform) AS platforms
               FROM content_items WHERE creator_id=?""", (creator_id,)).fetchone()
    md = complete(PACKET_PROMPT.format(
        fingerprint=json.dumps({k: v for k, v in profile.items() if k != "_meta"}, indent=1)[:12000],
        platforms="\n".join(f"{p['platform']}: {p['summary_json'][:800]}" for p in platforms) or "(youtube only)",
        stats=f"{stats['items']} items across {stats['platforms']} platforms, {stats['with_text'] or 0} with real text",
        name=name, creator_id=creator_id), max_tokens=6000, temperature=0.4, task="review_packet")
    out = PROFILE_DIR / f"{creator_id}_creator_review_packet.md"
    out.write_text(md)
    return str(out)
