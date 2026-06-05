"""Suggested products: "What product should I ask this creator about first?"

Exact products from recent product-related content, scored by
recency(30%) + relevance(35%) + frequency(15%) + engagement(10%) + confidence(10%).
Falls back to fingerprint-driven category/comparison prompts so the page is
never empty. Generic — driven by the creator fingerprint, no hard-coding.
"""
import json
import logging

from ..creator_fingerprint import get_fingerprint
from ..db import get_db, new_id, now
from ..llm import LLMError, complete_json
from .product_extractor import extract_creator_products

log = logging.getLogger("creator_twin.intelligence.suggestions")

PROMPT_FALLBACK = """This creator's identity:
{fingerprint}

Their actual recent content titles (this is their niche — trust these over assumptions):
{titles}

Generate {n} product suggestions a fan should ask THIS creator's AI twin about.

CRITICAL: Every suggestion MUST belong to this creator's exact niche as shown by their identity
and titles above. A cooking channel gets cookware/appliances/ingredients gear. A fitness-wearable
channel gets trackers/watches/rings. NEVER suggest generic consumer tech (SSDs, phones, earbuds)
unless tech is demonstrably their niche.

Mix of:
- specific popular products in their exact niche (real product names a fan could buy)
- comparison prompts ("X vs Y") between products they would realistically compare
- category prompts ("Budget [their-niche category]")

Return a JSON array:
[{{"product_name": "...", "product_brand": "or empty", "product_category": "...",
   "suggestion_type": "inferred_product|category_prompt|comparison_prompt",
   "reason_label": "Strong fit|Common topic|Frequently compared",
   "reason_detail": "one short line why this fits the creator"}}]"""


def _metric(metrics_json):
    m = json.loads(metrics_json or "{}")
    return max((int(m.get(k, 0) or 0) for k in ("views", "likes", "like_count")), default=0)


def generate_suggested_products(creator_id: str, limit: int = 8) -> list:
    extract_creator_products(creator_id)

    with get_db() as db:
        items = [dict(r) for r in db.execute(
            """SELECT content_id, platform, title, canonical_url, thumbnail_url, published_at,
                      products_mentioned_json, product_relevance_score, metrics_json
               FROM content_items WHERE creator_id=? AND is_product_related=1
               ORDER BY published_at DESC LIMIT 80""", (creator_id,)).fetchall()]

    # aggregate exact products
    agg = {}
    max_metric = max((_metric(i["metrics_json"]) for i in items), default=0) or 1
    for rank, it in enumerate(items):
        for name in json.loads(it["products_mentioned_json"] or "[]"):
            key = "".join(c for c in name.lower() if c.isalnum())
            a = agg.setdefault(key, {"name": name, "count": 0, "best": it, "best_rank": rank})
            a["count"] += 1
            if rank < a["best_rank"]:
                a["best"], a["best_rank"] = it, rank

    suggestions = []
    n_items = max(len(items), 1)
    max_count = max((a["count"] for a in agg.values()), default=1)
    for a in agg.values():
        it = a["best"]
        recency = 1 - a["best_rank"] / n_items
        relevance = float(it["product_relevance_score"] or 0.5)
        frequency = a["count"] / max_count
        engagement = _metric(it["metrics_json"]) / max_metric
        confidence = 0.9
        final = recency * 0.30 + relevance * 0.35 + frequency * 0.15 + engagement * 0.10 + confidence * 0.10
        title = it["title"] or ""
        reason = ("Recently reviewed" if any(w in title.lower() for w in ("review", "tested", "tried", "unboxing"))
                  else "Frequently compared" if " vs" in title.lower()
                  else "Mentioned recently")
        suggestions.append({
            "product_name": a["name"], "product_brand": a["name"].split()[0],
            "product_category": "", "product_url": "", "image_url": it["thumbnail_url"] or "",
            "price_text": "", "source_content_id": it["content_id"],
            "source_platform": it["platform"], "source_title": title[:160],
            "source_url": it["canonical_url"] or "",
            "reason_label": reason, "reason_detail": f"From: {title[:80]}" if title else "",
            "suggestion_type": "exact_product", "confidence": confidence,
            "recency_score": round(recency, 3), "relevance_score": round(relevance, 3),
            "final_score": round(final, 4),
        })
    suggestions.sort(key=lambda s: -s["final_score"])
    suggestions = suggestions[:limit]

    # fingerprint-driven prompts when exact products are thin
    if len(suggestions) < max(3, limit // 2):
        profile = get_fingerprint(creator_id) or {}
        with get_db() as db:
            all_titles = [r["title"] for r in db.execute(
                "SELECT title FROM content_items WHERE creator_id=? AND title != '' "
                "ORDER BY published_at DESC LIMIT 30", (creator_id,)).fetchall()]
        titles = "\n".join(f"- {t}" for t in all_titles)
        try:
            extra = complete_json(PROMPT_FALLBACK.format(
                fingerprint=json.dumps({k: profile.get(k) for k in
                                        ("one_sentence_identity", "creator_positioning",
                                         "primary_content_pillars", "recommended_tools",
                                         "products_services_offers", "target_audience")})[:3000],
                titles=titles or "(none)", n=limit - len(suggestions)), max_tokens=2500)
        except LLMError as e:
            log.warning("suggestion fallback LLM failed: %s", e)
            pillars = (profile.get("primary_content_pillars") or ["their main topic"])[:4]
            extra = [{"product_name": f"Ask about a new {p}", "product_category": str(p),
                      "suggestion_type": "category_prompt", "reason_label": "Common topic",
                      "reason_detail": "Core theme on this channel"} for p in pillars]
        for e in (extra if isinstance(extra, list) else []):
            if not e.get("product_name"):
                continue
            suggestions.append({
                "product_name": e["product_name"], "product_brand": e.get("product_brand", ""),
                "product_category": e.get("product_category", ""), "product_url": "",
                "image_url": "", "price_text": "", "source_content_id": None,
                "source_platform": "", "source_title": "", "source_url": "",
                "reason_label": e.get("reason_label", "Strong fit"),
                "reason_detail": e.get("reason_detail", ""),
                "suggestion_type": e.get("suggestion_type", "inferred_product"),
                "confidence": 0.5, "recency_score": 0.5, "relevance_score": 0.7,
                "final_score": 0.45,
            })

    suggestions = suggestions[:limit]

    # real product photos + buy links (Amazon search, cached, parallel)
    from ..product_images import lookup_many
    found = lookup_many([s["product_name"] for s in suggestions])
    for s in suggestions:
        hit = found.get(s["product_name"])
        if hit:
            s["image_url"] = hit["image"] or s["image_url"]
            s["product_url"] = s["product_url"] or hit["url"]

    with get_db() as db:
        db.execute("DELETE FROM suggested_products WHERE creator_id=?", (creator_id,))
        for s in suggestions:
            db.execute(
                """INSERT INTO suggested_products
                   (suggested_product_id, creator_id, product_name, product_brand, product_category,
                    product_url, image_url, price_text, source_content_id, source_platform,
                    source_title, source_url, reason_label, reason_detail, suggestion_type,
                    confidence, recency_score, relevance_score, final_score, metadata_json,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id("sp"), creator_id, s["product_name"], s["product_brand"],
                 s["product_category"], s["product_url"], s["image_url"], s["price_text"],
                 s["source_content_id"], s["source_platform"], s["source_title"], s["source_url"],
                 s["reason_label"], s["reason_detail"], s["suggestion_type"], s["confidence"],
                 s["recency_score"], s["relevance_score"], s["final_score"], "{}", now(), now()))
    return suggestions


def get_suggested_products(creator_id: str, limit: int = 8, regenerate: bool = False) -> list:
    if not regenerate:
        with get_db() as db:
            rows = [dict(r) for r in db.execute(
                """SELECT suggested_product_id, product_name, product_brand, product_category,
                          product_url, image_url, price_text, reason_label, reason_detail,
                          source_title, source_url, suggestion_type, confidence, final_score
                   FROM suggested_products WHERE creator_id=?
                   ORDER BY final_score DESC LIMIT ?""", (creator_id, limit)).fetchall()]
        if rows:
            return rows
    generate_suggested_products(creator_id, limit)
    return get_suggested_products(creator_id, limit, regenerate=False)
