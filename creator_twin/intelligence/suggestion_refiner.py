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

from ..config import ROOT as _ROOT  # noqa: F401  (loads .env first)

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

HQ_SYSTEM = (
    "You generate SPECIFIC product suggestions a fan would actually click, grounded in what this "
    "creator really covers. HARD RULES: 1) NEVER output generic filler like 'Beauty product deal', "
    "'Home gadget deal', 'Tech deal under $50', or anything ending in just 'deal' without a brand, "
    "retailer, or specific product. 2) Prefer brand + category ('Dove shampoo deals'), retailer + "
    "category ('CVS skincare deals'), named products ('Sol de Janeiro 62 dupes'), or specific "
    "models. 3) Every suggestion must trace to the creator's actual titles or taste model. "
    "4) Max 45 chars, clickable, no dates, no video-title copies. 5) why_relevant must cite the "
    "creator pattern it comes from in under 8 words.")


def enabled() -> bool:
    return USE_GEMINI and bool(GEMINI_API_KEY)


def refine_suggestions_with_gemini(creator: dict, raw_candidates: list, max_suggestions: int = 6,
                                   force: bool = False):
    """Returns (suggestions list or None, source). Cached; never raises."""
    if not ((enabled() or (force and GEMINI_API_KEY)) and raw_candidates):
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


_GENERIC_RE = None


def is_generic_filler(title: str) -> bool:
    """True for suggestions like 'Beauty product deal' / 'Tech deal under $50' that
    have no brand, retailer, or named product."""
    global _GENERIC_RE
    import re
    if _GENERIC_RE is None:
        _GENERIC_RE = re.compile(
            r"^(beauty|home|tech|kitchen|fitness|fashion|baby|pet|travel|gadget|product)?\s*"
            r"(product|gadget|item)?\s*deals?( under \$?\d+)?$", re.I)
    return bool(_GENERIC_RE.match((title or "").strip()))


def generate_high_quality_suggestions(creator_id: str, taste_model: dict, raw_candidates: list,
                                      titles: list = None, max_suggestions: int = 8):
    """One Gemini call grounded in the taste model + real titles. Returns
    (suggestions or None, source). Cached by creator+content hash; never raises."""
    if not GEMINI_API_KEY:
        return None, "deterministic"
    from ..db import get_db, now
    from ..db_writer import write
    from .suggested_products import is_valid_product_suggestion

    taste_model = taste_model or {}
    payload = {
        "creator_niche": taste_model.get("creator_niche", ""),
        "product_world": (taste_model.get("product_world") or [])[:6],
        "specific_product_categories": (taste_model.get("specific_product_categories") or [])[:8],
        "retailer_context": (taste_model.get("retailer_context") or [])[:5],
        "deal_logic": (taste_model.get("deal_logic") or [])[:4],
        "recent_titles": [(t or "")[:90] for t in (titles or [])[:40]],
        "raw_candidates": [{"title": c.get("product_name", ""),
                            "source_title": (c.get("source_title") or "")[:60]}
                           for c in (raw_candidates or [])[:20]],
        "good_examples": ["Dove shampoo deals", "CVS skincare deals", "Sol de Janeiro 62 dupes",
                          "Walgreens diaper stock-up"],
        "banned_examples": ["Beauty product deal", "Home gadget deal", "Tech deal under $50"],
    }
    content_hash = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    cache_key = f"hq:{creator_id}:{content_hash}"
    with get_db() as db:
        row = db.execute("SELECT suggestions_json FROM suggestion_cache WHERE cache_key=?",
                         (cache_key,)).fetchone()
    if row:
        return json.loads(row["suggestions_json"]), "gemini_hq_cached"

    prompt = (json.dumps(payload) +
              f"\n\nReturn ONLY JSON: {{\"suggestions\":[{{\"title\":\"...\",\"suggestion_type\":"
              f"\"exact_product|shopping_prompt|deal_prompt|category_prompt\",\"why_relevant\":"
              f"\"...\",\"reason_label\":\"2-3 word pill e.g. Beauty coupon\",\"query_text\":"
              f"\"...\",\"confidence\":0.0}}]}} with 5-{max_suggestions} SPECIFIC items.")
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "systemInstruction": {"parts": [{"text": HQ_SYSTEM}]},
                  "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.3}},
            timeout=25)
        if r.status_code != 200:
            log.warning("gemini hq suggestions failed %s — deterministic fallback", r.status_code)
            return None, "deterministic"
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        import re as _re
        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=_re.MULTILINE)
        suggestions = json.loads(text).get("suggestions", [])
    except Exception as e:
        log.warning("gemini hq suggestions error: %s — deterministic fallback", e)
        return None, "deterministic"

    clean = []
    for s in suggestions[:max_suggestions]:
        title = (s.get("title") or "")[:45]
        if is_valid_product_suggestion(title) and not is_generic_filler(title):
            clean.append({"product_name": title,
                          "suggestion_type": s.get("suggestion_type", "shopping_prompt"),
                          "reason_label": (s.get("reason_label") or "Good fit")[:24],
                          "reason_detail": (s.get("why_relevant") or "Matches their content")[:90],
                          "why_relevant": (s.get("why_relevant") or "")[:90],
                          "query_text": s.get("query_text") or title,
                          "confidence": float(s.get("confidence", 0.7))})
    if len(clean) < 3:
        return None, "deterministic"

    in_tok, out_tok = len(prompt) // 4, len(text) // 4
    write(lambda c: (
        c.execute("INSERT OR REPLACE INTO suggestion_cache (cache_key, provider, suggestions_json, created_at)"
                  " VALUES (?,?,?,?)", (cache_key, "gemini", json.dumps(clean), now())),
        c.execute("INSERT INTO llm_usage_logs (id, provider, model, task, creator_id, input_tokens, output_tokens, estimated_cost, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (hashlib.sha1(cache_key.encode()).hexdigest()[:20], "gemini", MODEL,
                   "hq_suggestions", creator_id, in_tok, out_tok,
                   round(in_tok * _IN_COST + out_tok * _OUT_COST, 6), now()))))
    return clean, "gemini_hq"
