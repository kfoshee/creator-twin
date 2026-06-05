"""Cross-platform creator fingerprint — the identity layer, built from
every connected source. Clearly separates confirmed / sourced / inferred facts."""
import json
import logging

from ..config import PROFILE_DIR
from ..db import get_db, now
from ..llm import complete_json
from ..prompts import FINGERPRINT_SYSTEM, XPLATFORM_FINGERPRINT_PROMPT

log = logging.getLogger("creator_twin.intelligence.fingerprint")


def _gather(creator_id: str):
    with get_db() as db:
        profiles = [dict(r) for r in db.execute(
            "SELECT platform, handle, bio, follower_count, post_count FROM creator_profiles WHERE creator_id=?",
            (creator_id,)).fetchall()]
        items = [dict(r) for r in db.execute(
            """SELECT platform, content_type, title, caption, description, text_body, metrics_json,
                      source_type, selected_for_deep_pass
               FROM content_items WHERE creator_id=?
               ORDER BY selected_for_deep_pass DESC, length(text_body) DESC LIMIT 250""",
            (creator_id,)).fetchall()]
        comments = [dict(r) for r in db.execute(
            "SELECT text, like_count FROM comments WHERE content_id IN "
            "(SELECT content_id FROM content_items WHERE creator_id=?) ORDER BY like_count DESC LIMIT 80",
            (creator_id,)).fetchall()]
        intake = db.execute(
            "SELECT intake_json FROM creator_intake WHERE creator_id=? ORDER BY created_at DESC LIMIT 1",
            (creator_id,)).fetchone()
        uploads = [dict(r) for r in db.execute(
            "SELECT title, text_body FROM content_items WHERE creator_id=? AND source_type='creator_uploaded' LIMIT 10",
            (creator_id,)).fetchall()]
    return profiles, items, comments, intake, uploads


def generate_fingerprint(creator_id: str) -> dict:
    profiles, items, comments, intake, uploads = _gather(creator_id)

    by_platform = {}
    for it in items:
        by_platform.setdefault(it["platform"], []).append(it)
    content_samples = ""
    for platform, its in by_platform.items():
        content_samples += f"\n--- {platform.upper()} ({len(its)} items) ---\n"
        for it in its[:40]:
            label = it["title"] or (it["caption"] or "")[:80] or (it["text_body"] or "")[:80]
            content_samples += f"- [{it['content_type']}] {label[:120]}\n"

    excerpts = "\n\n".join(
        f"--- {it['platform']}: {(it['title'] or '')[:80]} ---\n{it['text_body'][:2000]}"
        for it in items if it["text_body"])[:24000] or "(no real text available — infer from metadata)"

    intake_block = ""
    if intake:
        intake_block += "INTAKE FORM:\n" + intake["intake_json"][:4000] + "\n"
    for u in uploads:
        intake_block += f"UPLOAD '{u['title']}':\n{u['text_body'][:2000]}\n"
    intake_block = intake_block or "(none provided — public-content-only mode)"

    prompt = XPLATFORM_FINGERPRINT_PROMPT.format(
        profiles="\n".join(f"- {p['platform']}: @{p['handle']} | {p['follower_count']:,} followers | "
                           f"{(p['bio'] or '')[:300]}" for p in profiles) or "(none)",
        content_samples=content_samples[:14000],
        excerpts=excerpts,
        comments="\n".join(f"[{c['like_count']} likes] {c['text'][:250]}" for c in comments)[:8000] or "(none)",
        intake=intake_block[:10000])

    profile = complete_json(prompt, system=FINGERPRINT_SYSTEM, max_tokens=8000, task="deep_fingerprint")

    has_confirmed = bool(intake or uploads)
    has_real_text = any(it["text_body"] for it in items)
    source = "creator_confirmed_plus_ai_inferred" if has_confirmed else "ai_inferred_creator_fingerprint"
    confidence = "high" if (has_confirmed and has_real_text) else ("medium" if has_real_text else "low")

    with get_db() as db:
        db.execute(
            "INSERT INTO creator_fingerprint (creator_id, source_type, confidence, profile_json, approved_by_creator, created_at, updated_at)"
            " VALUES (?,?,?,?,0,?,?)", (creator_id, source, confidence, json.dumps(profile), now(), now()))
    (PROFILE_DIR / f"{creator_id}_fingerprint.json").write_text(json.dumps(profile, indent=2))
    return profile


def generate_gemini_starter_fingerprint(creator_id: str) -> dict:
    """SMART starter: builds the rich taste model + answer guidance (2 cached
    Gemini calls), Claude 0. Falls back to the deterministic starter on failure."""
    from .starter_taste_model import build_starter_taste_model
    taste, guidance, source = build_starter_taste_model(creator_id)
    if source != "gemini_starter" or not taste:
        log.warning("gemini starter unavailable — deterministic fallback")
        return generate_starter_fingerprint(creator_id)
    guidance = guidance or {}

    with get_db() as db:
        c = db.execute("SELECT channel_title FROM creators WHERE creator_id=?",
                       (creator_id,)).fetchone()
    name = (c["channel_title"] if c else "") or "this creator"

    conf = taste.get("confidence", 0.6)
    conf_label = ("high" if isinstance(conf, (int, float)) and conf >= 0.75
                  else "low" if isinstance(conf, (int, float)) and conf < 0.4
                  else conf if isinstance(conf, str) else "medium")
    profile = {
        "one_sentence_identity": f"{name}: {taste.get('creator_niche', '')}",
        "creator_positioning": taste.get("creator_niche", ""),
        "primary_content_pillars": (taste.get("product_world") or [])[:6],
        "deal_categories": (taste.get("specific_product_categories") or [])[:8],
        "retailer_context": (taste.get("retailer_context") or [])[:6],
        "deal_logic": (taste.get("deal_logic") or [])[:5],
        "target_audience": taste.get("audience", ""),
        "tone_style": "; ".join((taste.get("style_notes") or [])[:3]),
        "repeated_advice": (taste.get("likely_recommendation_patterns") or [])[:6],
        "recommendation_logic": "; ".join((taste.get("creator_buying_criteria") or [])[:4]),
        "product_take_rules": ((guidance.get("creator_specific_take_rules") or []) +
                               (guidance.get("voice_rules") or []))[:6],
        "price_value_rules": (guidance.get("price_rules") or [])[:4],
        "retailer_rules": (guidance.get("retailer_rules") or [])[:4],
        "answer_do": (guidance.get("answer_do") or [])[:5],
        "answer_dont": (guidance.get("answer_dont") or [])[:5],
        "boundaries_and_disallowed_claims": (taste.get("what_to_avoid") or [])[:4],
        "recommended_tools": [],
        "facts_needing_creator_review": ["starter profile built by Gemini from metadata"],
        "source_confidence": conf_label,
        "taste_model": taste,
        "answer_guidance": guidance,
    }
    with get_db() as db:
        db.execute(
            "INSERT INTO creator_fingerprint (creator_id, source_type, confidence, profile_json, approved_by_creator, created_at, updated_at)"
            " VALUES (?,?,?,?,0,?,?)",
            (creator_id, "gemini_starter", conf_label, json.dumps(profile), now(), now()))
    (PROFILE_DIR / f"{creator_id}_fingerprint.json").write_text(json.dumps(profile, indent=2))
    log.info("gemini starter fingerprint for %s (taste model + answer guidance)", creator_id)
    return profile


def generate_starter_fingerprint(creator_id: str) -> dict:
    """ZERO-LLM starter fingerprint from metadata: names, titles, keywords,
    extracted products. Good enough to ground takes; deep version is manual."""
    import re
    from collections import Counter
    profiles, items, _, _, _ = _gather(creator_id)
    with get_db() as db:
        c = db.execute("SELECT channel_title, channel_description FROM creators WHERE creator_id=?",
                       (creator_id,)).fetchone()
        prods = [p for r in db.execute(
            "SELECT products_mentioned_json FROM content_items WHERE creator_id=? LIMIT 100",
            (creator_id,)).fetchall() for p in json.loads(r[0] or "[]")]
    name = (c["channel_title"] if c else "") or "this creator"
    desc = ((c["channel_description"] if c else "") or "")[:600]
    titles = [it["title"] or it["caption"] or "" for it in items[:40]]

    words = Counter(w for t in titles for w in re.findall(r"[a-z]{4,}", t.lower())
                    if w not in ("with", "this", "that", "your", "from", "best", "video", "2024", "2025", "2026"))
    keywords = [w for w, _ in words.most_common(10)]
    blob = (desc + " " + " ".join(keywords)).lower()
    niche = ("deals & coupons" if re.search(r"deal|coupon|bargain|discount", blob)
             else "wearables & health tech" if re.search(r"fitness|sleep|tracker|smartwatch|health", blob)
             else "cooking & kitchen" if re.search(r"cook|kitchen|pizza|recipe|food", blob)
             else "consumer tech" if re.search(r"tech|gadget|pc|laptop|review|vacuum", blob)
             else "beauty" if re.search(r"beauty|skincare|makeup", blob)
             else ", ".join(keywords[:3]) or "general products")

    profile = {
        "one_sentence_identity": f"{name} covers {niche}" + (f" — {desc[:120]}" if desc else ""),
        "creator_positioning": f"{niche} creator",
        "primary_content_pillars": keywords[:5] or [niche],
        "target_audience": f"people shopping for {niche}",
        "tone_style": "direct, practical, value-focused",
        "recommended_tools": list(dict.fromkeys(prods))[:8],
        "products_services_offers": [],
        "repeated_advice": ["compare price vs real performance", "avoid paying for branding alone"],
        "boundaries_and_disallowed_claims": ["never claim firsthand testing without source data"],
        "recommendation_logic": "value for money first; verify claims before trusting listings",
        "facts_needing_creator_review": ["entire starter profile is metadata-inferred"],
        "source_confidence": "low",
    }
    with get_db() as db:
        db.execute(
            "INSERT INTO creator_fingerprint (creator_id, source_type, confidence, profile_json, approved_by_creator, created_at, updated_at)"
            " VALUES (?,?,?,?,0,?,?)",
            (creator_id, "deterministic_starter", "low", json.dumps(profile), now(), now()))
    (PROFILE_DIR / f"{creator_id}_fingerprint.json").write_text(json.dumps(profile, indent=2))
    log.info("deterministic starter fingerprint for %s (niche: %s, 0 LLM calls)", creator_id, niche)
    return profile


# re-export helpers used everywhere (single source of truth lives in legacy module)
from ..creator_fingerprint import fingerprint_summary, get_fingerprint  # noqa: E402,F401
