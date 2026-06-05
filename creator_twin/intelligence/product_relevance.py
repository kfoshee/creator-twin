"""Product relevance classifier — rule-based first, cheap and fast.

Scores every content item 0..1 from title/description/caption/hashtags/
transcript/comments. LLM classification is reserved for borderline deep-pass
items (optional, off by default to keep the pass free).
"""
import json
import logging
import re

from ..db import get_db, now

log = logging.getLogger("creator_twin.intelligence.product_relevance")

TITLE_SIGNALS = [
    "review", "unboxing", "setup", "gear", "best", "top", "favorite", "favorites",
    "tools", "products", "amazon", "haul", "deal", "bought", "buy", "worth it",
    "vs", "comparison", "compared", "tested", "tried", "recommend", "recommendation",
    "sponsored", "affiliate", "desk setup", "camera", "phone", "laptop", "ai tools",
    "software", "app", "gadget", "kitchen", "beauty", "fashion", "tech", "budget",
    "cheap", "upgrade", "accessories", "what i use",
    # food / home / lifestyle gear
    "oven", "knife", "pan", "skillet", "cookware", "blender", "espresso", "grill",
    "ingredient", "flour", "mixer", "air fryer", "must have", "essentials",
]
DESC_SIGNALS = [
    "affiliate", "amzn.to", "amazon.com", "link below", "links below", "my gear",
    "shop", "use code", "discount code", "promo code", "sponsor", "partner link",
    "commission", "geni.us", "kit.co",
]
HASHTAG_SIGNALS = [
    "amazonfinds", "tiktokshop", "review", "unboxing", "haul", "techreview",
    "deals", "affiliate", "giftideas", "setup", "gadgets", "musthaves",
]
COMMENT_SIGNALS = [
    "link?", "where did you get", "what product", "is it worth", "which one should i buy",
    "where can i buy", "how much", "what's the price", "link please", "product name",
]
TEXT_SIGNALS = [
    "i recommend", "you should buy", "worth the money", "not worth", "overpriced",
    "great value", "price point", "alternative", "cheaper option", "i use this",
    "my favorite", "discount", "$",
]


def score_item(item: dict, comment_text: str = "") -> tuple:
    """Return (score 0..1, reasons list, likely_products list)."""
    reasons, score = [], 0.0
    title = (item.get("title") or "").lower()
    desc = (item.get("description") or "").lower()
    caption = (item.get("caption") or "").lower()
    text = ((item.get("text_body") or "")[:8000]).lower()
    hashtags = [h.lower() for h in json.loads(item.get("hashtags_json") or "[]")]
    comments = comment_text.lower()

    t_hits = [s for s in TITLE_SIGNALS if s in title or s in caption]
    if t_hits:
        score += min(0.25 + 0.1 * len(t_hits), 0.55)
        reasons.append(f"title/caption signals: {', '.join(t_hits[:5])}")
    d_hits = [s for s in DESC_SIGNALS if s in desc]
    if d_hits:
        score += min(0.2 + 0.1 * len(d_hits), 0.4)
        reasons.append(f"description signals: {', '.join(d_hits[:5])}")
    h_hits = [s for s in HASHTAG_SIGNALS if any(s in h for h in hashtags)]
    if h_hits:
        score += 0.15
        reasons.append(f"hashtags: {', '.join(h_hits[:5])}")
    c_hits = [s for s in COMMENT_SIGNALS if s in comments]
    if c_hits:
        score += 0.15
        reasons.append(f"audience asking: {', '.join(c_hits[:3])}")
    x_hits = [s for s in TEXT_SIGNALS if s in text]
    if x_hits:
        score += min(0.1 + 0.05 * len(x_hits), 0.3)
        reasons.append(f"transcript/body signals: {', '.join(x_hits[:5])}")

    # crude product-name guesses: Brand Model123 patterns in title
    likely = re.findall(r"\b([A-Z][a-zA-Z]+ [A-Z]?[a-zA-Z]*\s?[A-Z0-9][\w-]+)\b",
                        item.get("title") or "")[:5]
    return min(score, 1.0), reasons, likely


def classify_creator_content(creator_id: str, threshold: float = 0.35, progress=None) -> dict:
    """Score every content item; store results. Returns counts.

    Lock-safe: reads once, classifies fully in memory (no DB held), then
    writes in short batches through the single-writer queue.
    """
    from ..db_writer import write

    # 1. read everything in one short connection, then release the DB
    with get_db() as db:
        items = [dict(r) for r in db.execute(
            """SELECT content_id, title, description, caption, text_body, hashtags_json
               FROM content_items WHERE creator_id=?""", (creator_id,)).fetchall()]
        comment_map = {r["content_id"]: r["texts"] or "" for r in db.execute(
            """SELECT content_id, GROUP_CONCAT(text, ' | ') AS texts FROM comments
               WHERE content_id IN (SELECT content_id FROM content_items WHERE creator_id=?)
               GROUP BY content_id""", (creator_id,)).fetchall()}

    # 2. classify in memory — no transaction open
    results, n_product = [], 0
    for item in items:
        try:
            score, reasons, likely = score_item(item, comment_map.get(item["content_id"], ""))
        except Exception as e:
            log.warning("classification failed for %s: %s", item["content_id"], e)
            results.append((0, 0, "[]", "[]", "failed_product_classification", item["content_id"]))
            continue
        is_product = 1 if score >= threshold else 0
        n_product += is_product
        results.append((is_product, round(score, 3), json.dumps(reasons),
                        json.dumps(likely), None, item["content_id"]))

    # 3. batched writes via single-writer queue (short transactions)
    BATCH = 100
    for i in range(0, len(results), BATCH):
        batch = results[i:i + BATCH]

        def apply(conn, batch=batch):
            for is_p, score, reasons, likely, err, cid in batch:
                conn.execute(
                    """UPDATE content_items SET is_product_related=?, product_relevance_score=?,
                       product_relevance_reasons_json=?, likely_products_json=? WHERE content_id=?""",
                    (is_p, score, reasons, likely, cid))
                if err:
                    conn.execute("UPDATE content_items SET processing_error=? WHERE content_id=?",
                                 (err, cid))
        write(apply, wait=True)
        if progress:
            progress(f"Scored {min(i + BATCH, len(results))}/{len(results)} items for product relevance")
    return {"total": len(items), "product_candidates": n_product}
