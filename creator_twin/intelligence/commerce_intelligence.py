"""Commerce intelligence: products, brands, CTAs, recommendation style.

Never invents sales/revenue numbers — only what's visible in content.
"""
import json
import logging
import re

from ..config import PROFILE_DIR
from ..creator_fingerprint import get_fingerprint
from ..db import get_db
from ..llm import LLMError, complete_json
from ..prompts import COMMERCE_PROMPT

log = logging.getLogger("creator_twin.intelligence.commerce")

COMMERCE_HINTS = re.compile(
    r"\b(affiliate|sponsor|discount|code|link in bio|deal|promo|review|unboxing|"
    r"amazon|shop|buy|price|\$\d|sale|partner)\b", re.IGNORECASE)


def generate_commerce_intelligence(creator_id: str) -> dict:
    profile = get_fingerprint(creator_id) or {}
    with get_db() as db:
        items = db.execute(
            """SELECT platform, title, caption, description, text_body FROM content_items
               WHERE creator_id=? ORDER BY selected_for_deep_pass DESC LIMIT 150""",
            (creator_id,)).fetchall()
    samples = ""
    for it in items:
        blob = " ".join(filter(None, [it["title"], it["caption"], (it["description"] or "")[:300],
                                      (it["text_body"] or "")[:500]]))
        if COMMERCE_HINTS.search(blob):
            samples += f"\n--- {it['platform']} ---\n{blob[:800]}\n"
    if not samples:
        log.info("no commerce signals found for %s", creator_id)
        return {}
    try:
        model = complete_json(COMMERCE_PROMPT.format(
            fingerprint=json.dumps({k: v for k, v in profile.items() if k != '_meta'})[:4000],
            samples=samples[:16000]), max_tokens=4000)
    except LLMError as e:
        log.warning("commerce intelligence failed: %s", e)
        return {}
    (PROFILE_DIR / f"{creator_id}_commerce.json").write_text(json.dumps(model, indent=2))
    return model
