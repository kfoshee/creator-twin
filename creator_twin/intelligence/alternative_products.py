"""Better alternatives when the twin says pass/overpriced/cautious.

Sources, in order: the creator's own suggested/extracted products in the
same category, live Amazon search, and search-prompt suggestions as a floor.
"""
import logging
import re

from ..db import get_db

log = logging.getLogger("creator_twin.intelligence.alternatives")

STOP = {"the", "with", "for", "and", "pack", "of", "new", "pro", "set", "inch"}


def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if t not in STOP}


def get_alternatives_for_product(creator_id: str, product_title: str,
                                 category: str = "", max_results: int = 4) -> list:
    out, seen = [], set()
    ptoks = _tokens(product_title) | _tokens(category)

    # 1. creator's own suggested products in a similar category
    with get_db() as db:
        rows = db.execute(
            """SELECT product_name, product_brand, product_url, image_url, price_text,
                      reason_label, source_title FROM suggested_products
               WHERE creator_id=? ORDER BY final_score DESC LIMIT 20""", (creator_id,)).fetchall()
    for r in rows:
        overlap = len(_tokens(r["product_name"]) & ptoks)
        if overlap >= 1 and r["product_name"].lower() not in product_title.lower():
            key = r["product_name"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "product_name": r["product_name"], "brand": r["product_brand"] or "",
                "product_url": r["product_url"] or "", "image_url": r["image_url"] or "",
                "price_text": r["price_text"] or "",
                "reason": r["source_title"][:80] if r["source_title"] else "From this creator's catalog",
                "relation": "creator_reviewed", "confidence": 0.8,
            })

    # 2. live Amazon search for better-value options
    if len(out) < max_results:
        try:
            from ..product_search import search_products
            cat = category or " ".join(list(ptoks)[:4])
            for r in search_products(f"best budget {cat}", limit=6):
                key = r["title"].lower()[:50]
                if key in seen or _tokens(r["title"]) == _tokens(product_title):
                    continue
                seen.add(key)
                out.append({
                    "product_name": r["title"][:120], "brand": r["brand"],
                    "product_url": r["product_url"], "image_url": r["image_url"],
                    "price_text": r["price_text"],
                    "reason": (f"★{r['rating']} ({r['review_count']:,})"
                               if r.get("rating") and r.get("review_count") else "Better-value pick"),
                    "relation": "better_value", "confidence": 0.6,
                })
                if len(out) >= max_results:
                    break
        except Exception as e:
            log.warning("alternative search failed: %s", e)

    # 3. floor: search prompts
    if not out:
        base = category or product_title[:40]
        out = [{"product_name": q, "brand": "", "product_url": "", "image_url": "",
                "price_text": "", "reason": "Search this instead",
                "relation": "search_suggestion", "confidence": 0.4}
               for q in (f"budget {base}", f"{base} alternative", f"best {base} under $50")]
    return out[:max_results]
