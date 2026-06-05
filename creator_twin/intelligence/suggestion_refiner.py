"""Optional one-call Gemini suggestion cleanup. NEVER Claude, off by default.

Enabled only when GEMINI_API_KEY is set and USE_GEMINI_FOR_SUGGESTIONS=true.
One tiny call per build (compact candidates in, ≤6 suggestions out), cached
by creator+content hash, usage logged. Any failure → deterministic fallback.
"""
import hashlib
import json
import logging
import os

import requests

log = logging.getLogger("creator_twin.suggestion_refiner")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
USE_GEMINI = os.environ.get("USE_GEMINI_FOR_SUGGESTIONS", "false").lower() == "true"
MODEL = os.environ.get("GEMINI_SUGGESTION_MODEL", "gemini-2.5-flash-lite")
MAX_CALLS = int(os.environ.get("MAX_GEMINI_SUGGESTION_CALLS_PER_BUILD", 1))
# flash-lite pricing for telemetry
_IN_COST, _OUT_COST = 0.075 / 1_000_000, 0.30 / 1_000_000

SYSTEM = ("You are cleaning product suggestion labels for a creator-commerce app. Return only "
          "real products, product categories, or shopping prompts a user could ask about. "
          "Never return dates, comments, generic post labels, or raw video titles unless "
          "clearly product-related. Keep suggestions short (max 45 chars) and clickable.")


def enabled() -> bool:
    return USE_GEMINI and bool(GEMINI_API_KEY)


def refine_suggestions_with_gemini(creator: dict, raw_candidates: list, max_suggestions: int = 6):
    """Returns (suggestions list or None, source). Cached; never raises."""
    if not enabled() or not raw_candidates:
        return None, "deterministic"
    from ..db import get_db, now
    from ..db_writer import write
    from .suggested_products import is_valid_product_suggestion

    payload = {
        "creator_name": creator.get("name", ""),
        "creator_niche_guess": creator.get("niche", ""),
        "raw_candidates": [{"title": c.get("product_name", ""),
                            "source_title": c.get("source_title", "")[:60]}
                           for c in raw_candidates[:20]],
        "rejected_junk_examples": ["Posted 5", "Comment 2", "June 2nd", "Part 2"],
    }
    content_hash = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    cache_key = f"{creator.get('id', '')}:{content_hash}"

    with get_db() as db:
        row = db.execute("SELECT suggestions_json FROM suggestion_cache WHERE cache_key=?",
                         (cache_key,)).fetchone()
    if row:
        return json.loads(row["suggestions_json"]), "gemini_refined_cached"

    prompt = (json.dumps(payload) +
              f"\n\nReturn ONLY JSON: {{\"suggestions\":[{{\"title\":\"...\",\"suggestion_type\":"
              f"\"exact_product|shopping_prompt|deal_prompt|category_prompt\",\"reason_label\":\"...\","
              f"\"query_text\":\"...\",\"confidence\":0.0}}]}} with 3-{max_suggestions} items.")
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "systemInstruction": {"parts": [{"text": SYSTEM}]},
                  "generationConfig": {"maxOutputTokens": 800, "temperature": 0.3}},
            timeout=20)
        if r.status_code != 200:
            log.warning("gemini refine failed %s — deterministic fallback", r.status_code)
            return None, "deterministic"
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        import re as _re
        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=_re.MULTILINE)
        suggestions = json.loads(text).get("suggestions", [])
    except Exception as e:
        log.warning("gemini refine error: %s — deterministic fallback", e)
        return None, "deterministic"

    clean = []
    for s in suggestions[:max_suggestions]:
        title = (s.get("title") or "")[:45]
        if is_valid_product_suggestion(title):
            clean.append({"product_name": title,
                          "suggestion_type": s.get("suggestion_type", "shopping_prompt"),
                          "reason_label": s.get("reason_label", "Good fit"),
                          "reason_detail": "Refined from recent titles",
                          "query_text": s.get("query_text") or title,
                          "confidence": float(s.get("confidence", 0.7))})
    if not clean:
        return None, "deterministic"

    in_tok, out_tok = len(prompt) // 4, len(text) // 4
    write(lambda c: (
        c.execute("INSERT OR REPLACE INTO suggestion_cache (cache_key, provider, suggestions_json, created_at)"
                  " VALUES (?,?,?,?)", (cache_key, "gemini", json.dumps(clean), now())),
        c.execute("INSERT INTO llm_usage_logs (id, provider, model, task, creator_id, input_tokens, output_tokens, estimated_cost, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (hashlib.sha1(cache_key.encode()).hexdigest()[:20], "gemini", MODEL,
                   "suggestion_cleanup", creator.get("id", ""), in_tok, out_tok,
                   round(in_tok * _IN_COST + out_tok * _OUT_COST, 6), now()))))
    return clean, "gemini_refined"
