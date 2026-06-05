"""Audience model: personas, questions, objections, content gaps."""
import json
import logging

from ..config import PROFILE_DIR
from ..creator_fingerprint import get_fingerprint
from ..db import get_db
from ..llm import LLMError, complete_json
from ..prompts import AUDIENCE_MODEL_PROMPT

log = logging.getLogger("creator_twin.intelligence.audience")


def generate_audience_model(creator_id: str) -> dict:
    profile = get_fingerprint(creator_id) or {}
    with get_db() as db:
        comments = db.execute(
            """SELECT text, like_count FROM comments WHERE content_id IN
               (SELECT content_id FROM content_items WHERE creator_id=?)
               ORDER BY like_count DESC LIMIT 120""", (creator_id,)).fetchall()
    try:
        model = complete_json(AUDIENCE_MODEL_PROMPT.format(
            fingerprint=json.dumps({k: v for k, v in profile.items() if k != '_meta'})[:5000],
            comments="\n".join(f"[{c['like_count']}] {c['text'][:200]}" for c in comments)[:12000]
                     or "(no comments — infer from fingerprint)"), max_tokens=4000, task="creator_style_synthesis")
    except LLMError as e:
        log.warning("audience model failed: %s", e)
        return {}
    (PROFILE_DIR / f"{creator_id}_audience_model.json").write_text(json.dumps(model, indent=2))
    return model
