"""Suggested products: "What product should I ask this creator about first?"

Exact products from recent product-related content, scored by
recency(30%) + relevance(35%) + frequency(15%) + engagement(10%) + confidence(10%).
Falls back to fingerprint-driven category/comparison prompts so the page is
never empty. Generic — driven by the creator fingerprint, no hard-coding.
"""
import json
import logging
import re

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


_MONTHS = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)"
_INVALID_RX = [
    re.compile(r"^\s*comments?\s*#?\d*\s*$", re.I),
    re.compile(r"^\s*(part|episode|ep|day|week|video|post|reel|shorts?|upload|vlog|stream|live|update|q\s*&?\s*a|haul)\s*#?\d*\s*$", re.I),
    re.compile(rf"^\s*{_MONTHS}\.?\s+\d{{1,2}}(st|nd|rd|th)?\s*$", re.I),
    re.compile(rf"^\s*\d{{1,2}}(st|nd|rd|th)?\s+(of\s+)?{_MONTHS}\s*$", re.I),
    re.compile(r"^\s*(today|yesterday|tonight|tomorrow|this week)('s)?\s*$", re.I),
    re.compile(r"^\s*(posted|uploaded|new video|live|premiere)\s*#?\d*\s*$", re.I),
    re.compile(r"^[\W\d\s]+$"),  # only digits/punctuation
]
_GENERIC_SINGLE = {"video", "post", "reel", "short", "upload", "deal", "deals", "stuff",
                   "things", "items", "finds", "find", "products", "product", "amazon",
                   "link", "links", "today", "posted"}

_RETAILERS = ["cvs", "walgreens", "target", "walmart", "amazon", "costco", "ulta",
              "sephora", "dollar tree", "kroger", "aldi", "sam's club"]
_DEAL_CATS = ["skincare", "shampoo", "body wash", "makeup", "beauty", "cleaning", "laundry",
              "household", "kitchen", "grocery", "snack", "vitamins", "diapers", "haircare",
              "fragrance", "candle", "detergent", "toothpaste", "razor", "deodorant"]


def candidates_from_titles(titles: list) -> list:
    """Turn raw titles into clickable shopping prompts — deterministic, $0.

    'CVS Beauty Event Skincare Deals' -> 'CVS skincare deals'
    'Sol de Janeiro Body Butter Dupes at...' -> 'Sol de Janeiro body butter dupes'
    """
    out, seen = [], set()

    def add(title, stype, reason):
        title = re.sub(r"\s+", " ", title).strip()[:45]
        key = title.lower()
        if key in seen or not is_valid_product_suggestion(title):
            return
        seen.add(key)
        out.append({"product_name": title, "suggestion_type": stype,
                    "reason_label": reason, "reason_detail": "From recent titles"})

    for t in titles:
        if not t:
            continue
        low = t.lower()
        m = re.search(r"([A-Z][A-Za-z'&. ]{2,40}?)\s+[Dd]upes?\b", t)
        if m:
            add(f"{m.group(1).strip()} dupes", "shopping_prompt", "Dupe find")
        for r in _RETAILERS:
            if r in low:
                for c in _DEAL_CATS:
                    if c in low:
                        add(f"{r.upper() if r == 'cvs' else r.title()} {c} deals",
                            "deal_prompt", "Deal idea")
        m2 = re.search(r"\b([A-Z][a-z]{2,12})\s+(shampoo|body wash|skincare|makeup|detergent|"
                       r"toothpaste|razors?|deodorant|lotion|serum)\b", t)
        if m2:
            add(f"{m2.group(1)} {m2.group(2)} deal", "deal_prompt", "Deal idea")
    return out[:8]

VALID_TYPES = ("exact_product", "inferred_product", "product_category",
               "category_prompt", "comparison_prompt", "shopping_prompt", "deal_prompt")


def is_valid_product_suggestion(title: str, creator_name: str = "") -> bool:
    """Reject comments, dates, video-part labels, and other non-products."""
    t = (title or "").strip()
    if len(t) < 4:
        return False
    if creator_name and t.lower() == creator_name.lower():
        return False
    for rx in _INVALID_RX:
        if rx.search(t):
            return False
    words = t.split()
    if len(words) == 1 and words[0].lower() in _GENERIC_SINGLE:
        return False
    return True


def get_fallback_suggestions_for_creator(fingerprint: dict) -> list:
    """Deterministic niche prompts — zero LLM calls."""
    blob = " ".join(str(fingerprint.get(k, "")) for k in
                    ("one_sentence_identity", "creator_positioning",
                     "primary_content_pillars", "target_audience")).lower()

    def mk(titles, stype, label):
        return [{"product_name": t, "suggestion_type": stype, "reason_label": label,
                 "reason_detail": "From this creator's niche"} for t in titles]

    if re.search(r"deal|coupon|bargain|discount|amazon finds|saving", blob):
        return mk(["Best Amazon deal today", "Kitchen gadget deal", "Robot vacuum deal",
                   "Tech deal under $50", "Home gadget deal", "Beauty product deal"],
                  "deal_prompt", "Deal idea")
    if re.search(r"wearable|fitness|sleep|health|tracker|smartwatch|ring", blob):
        return mk(["Smartwatch", "Sleep tracker", "Fitness tracker", "Smart ring",
                   "Heart-rate monitor"], "shopping_prompt", "Good fit")
    if re.search(r"cook|kitchen|pizza|recipe|chef|food|bak", blob):
        return mk(["Pizza oven", "Chef knife", "Stand mixer", "Air fryer",
                   "Cast iron skillet"], "shopping_prompt", "Good fit")
    if re.search(r"tech|gadget|pc|laptop|review|smart home|vacuum", blob):
        return mk(["Laptop", "Wireless headphones", "Smartwatch", "Power bank",
                   "Robot vacuum"], "shopping_prompt", "Good fit")
    if re.search(r"beauty|skincare|makeup|hair", blob):
        return mk(["Skincare set", "Hair dryer", "LED face mask", "Makeup organizer"],
                  "shopping_prompt", "Good fit")
    return mk(["Best Amazon deal today", "Wireless earbuds", "Top-rated kitchen gadget",
               "Portable charger"], "shopping_prompt", "Popular pick")


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

    with get_db() as db:
        crow = db.execute("SELECT channel_title FROM creators WHERE creator_id=?", (creator_id,)).fetchone()
    creator_name = (crow["channel_title"] if crow else "") or ""

    suggestions = []
    n_items = max(len(items), 1)
    max_count = max((a["count"] for a in agg.values()), default=1)
    for a in agg.values():
        if not is_valid_product_suggestion(a["name"], creator_name):
            log.info("rejected suggestion candidate: %r", a["name"])
            continue
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

    # title-derived shopping prompts (deterministic, $0): "CVS skincare deals", "X dupes"...
    with get_db() as db:
        recent_titles = [r["title"] for r in db.execute(
            "SELECT title FROM content_items WHERE creator_id=? AND title != '' "
            "ORDER BY published_at DESC LIMIT 30", (creator_id,)).fetchall()]
    profile = get_fingerprint(creator_id) or {}
    suggestion_source = "deterministic"
    for cand in candidates_from_titles(recent_titles):
        if len(suggestions) >= limit:
            break
        if any(s["product_name"].lower() == cand["product_name"].lower() for s in suggestions):
            continue
        suggestions.append({
            "product_name": cand["product_name"], "product_brand": "", "product_category": "",
            "product_url": "", "image_url": "", "price_text": "", "source_content_id": None,
            "source_platform": "", "source_title": "", "source_url": "",
            "reason_label": cand["reason_label"], "reason_detail": cand["reason_detail"],
            "suggestion_type": cand["suggestion_type"], "confidence": 0.6,
            "recency_score": 0.6, "relevance_score": 0.6, "final_score": 0.5,
        })

    # optional single Gemini cleanup call (off by default; NEVER Claude)
    from .suggestion_refiner import enabled as gemini_enabled, refine_suggestions_with_gemini
    if gemini_enabled():
        refined, suggestion_source = refine_suggestions_with_gemini(
            {"id": creator_id, "name": creator_name,
             "niche": str(profile.get("creator_positioning", ""))[:80]},
            suggestions + [{"product_name": t, "source_title": t} for t in recent_titles[:10]],
            max_suggestions=limit)
        if refined:
            exact_keep = [s for s in suggestions if s["suggestion_type"] in ("exact_product", "inferred_product")][:2]
            suggestions = exact_keep + [{
                "product_name": r["product_name"], "product_brand": "", "product_category": "",
                "product_url": "", "image_url": "", "price_text": "", "source_content_id": None,
                "source_platform": "", "source_title": "", "source_url": "",
                "reason_label": r["reason_label"], "reason_detail": r["reason_detail"],
                "suggestion_type": r["suggestion_type"], "confidence": r.get("confidence", 0.7),
                "recency_score": 0.6, "relevance_score": 0.7, "final_score": 0.55,
            } for r in refined if not any(
                s["product_name"].lower() == r["product_name"].lower() for s in exact_keep)]

    # deterministic niche fallback (zero AI cost), LLM only as last resort
    if len(suggestions) < max(3, limit // 2):
        for f in get_fallback_suggestions_for_creator(profile):
            if len(suggestions) >= limit:
                break
            if any(s["product_name"].lower() == f["product_name"].lower() for s in suggestions):
                continue
            suggestions.append({
                "product_name": f["product_name"], "product_brand": "",
                "product_category": "", "product_url": "", "image_url": "", "price_text": "",
                "source_content_id": None, "source_platform": "", "source_title": "",
                "source_url": "", "reason_label": f["reason_label"],
                "reason_detail": f["reason_detail"], "suggestion_type": f["suggestion_type"],
                "confidence": 0.5, "recency_score": 0.5, "relevance_score": 0.6,
                "final_score": 0.4,
            })

    if len(suggestions) < max(3, limit // 2):
        with get_db() as db:
            all_titles = [r["title"] for r in db.execute(
                "SELECT title FROM content_items WHERE creator_id=? AND title != '' "
                "ORDER BY published_at DESC LIMIT 30", (creator_id,)).fetchall()]
        titles = "\n".join(f"- {t}" for t in all_titles)
        try:
            from ..llm_router import allowed
            if not allowed("suggested_products"):
                raise LLMError("router: suggestions are deterministic-only")
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
            if not e.get("product_name") or not is_valid_product_suggestion(e["product_name"], creator_name):
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

    # real product photos + buy links — ONLY for exact products (prompts get an icon, not a fake photo)
    from ..product_images import lookup_many
    exact = [s for s in suggestions if s["suggestion_type"] in ("exact_product", "inferred_product")]
    found = lookup_many([s["product_name"] for s in exact])
    for s in exact:
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
                 s["recency_score"], s["relevance_score"], s["final_score"],
                 json.dumps({"source": suggestion_source}), now(), now()))
    return suggestions


def get_suggested_products(creator_id: str, limit: int = 8, regenerate: bool = False) -> list:
    if not regenerate:
        with get_db() as db:
            rows = [dict(r) for r in db.execute(
                """SELECT suggested_product_id, product_name, product_brand, product_category,
                          product_url, image_url, price_text, reason_label, reason_detail,
                          source_title, source_url, suggestion_type, confidence, final_score,
                          metadata_json
                   FROM suggested_products WHERE creator_id=?
                   ORDER BY final_score DESC LIMIT ?""", (creator_id, limit)).fetchall()]
        # filter stale junk at the read path too; regenerate if the stored set is bad/thin
        valid = [r for r in rows if is_valid_product_suggestion(r["product_name"])
                 and r["suggestion_type"] in VALID_TYPES]
        for r in rows:
            if r not in valid:
                log.info("filtered stored suggestion: %r", r["product_name"])
        if len(valid) >= 3 or (rows and len(valid) == len(rows)):
            for v in valid:
                v["query_text"] = v["product_name"]
                v["suggestion_source"] = json.loads(v.pop("metadata_json", None) or "{}").get("source", "deterministic")
            return valid
    generate_suggested_products(creator_id, limit)
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            """SELECT suggested_product_id, product_name, product_brand, product_category,
                      product_url, image_url, price_text, reason_label, reason_detail,
                      source_title, source_url, suggestion_type, confidence, final_score,
                      metadata_json
               FROM suggested_products WHERE creator_id=?
               ORDER BY final_score DESC LIMIT ?""", (creator_id, limit)).fetchall()]
    out = [r for r in rows if is_valid_product_suggestion(r["product_name"])]
    for v in out:
        v["query_text"] = v["product_name"]
        v["suggestion_source"] = json.loads(v.pop("metadata_json", None) or "{}").get("source", "deterministic")
    return out
