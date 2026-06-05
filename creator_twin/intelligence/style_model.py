"""Style model: how the creator writes/speaks, per platform, for drafting."""
import json
import logging

from ..config import PROFILE_DIR
from ..creator_fingerprint import get_fingerprint
from ..db import get_db
from ..llm import LLMError, complete_json
from ..prompts import STYLE_MODEL_PROMPT

log = logging.getLogger("creator_twin.intelligence.style")


def generate_style_model(creator_id: str) -> dict:
    profile = get_fingerprint(creator_id) or {}
    with get_db() as db:
        items = db.execute(
            """SELECT platform, title, caption, text_body FROM content_items
               WHERE creator_id=? AND (text_body != '' OR caption != '')
               ORDER BY selected_for_deep_pass DESC LIMIT 30""", (creator_id,)).fetchall()
    samples = ""
    for it in items:
        text = it["text_body"] or it["caption"]
        samples += f"\n--- {it['platform']} ---\n{text[:1200]}\n"
    if not samples:
        samples = "(no real text — derive cautiously from fingerprint only)"
    try:
        model = complete_json(STYLE_MODEL_PROMPT.format(
            fingerprint=json.dumps({k: v for k, v in profile.items() if k != '_meta'})[:5000],
            samples=samples[:18000]), max_tokens=4000, task="creator_style_synthesis")
    except LLMError as e:
        log.warning("style model failed: %s", e)
        return {}
    (PROFILE_DIR / f"{creator_id}_style_model.json").write_text(json.dumps(model, indent=2))
    return model
