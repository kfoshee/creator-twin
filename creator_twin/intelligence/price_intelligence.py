"""Price intelligence: deterministic price context, never Claude.

Builds comparable prices (cached Amazon search), a fair-price range, and
retailer check-price actions so the answer can DO the comparison work
instead of telling the user to go shop around.
"""
import logging
import re

from ..price_validation import parse_price_to_cents
from .retailer_links import (amazon_search_url, cvs_search_url, google_shopping_search_url,
                             target_search_url, walgreens_search_url, walmart_search_url)

log = logging.getLogger("creator_twin.price_intelligence")

_DRUGSTORE = re.compile(r"\b(shampoo|conditioner|body wash|lotion|serum|moisturizer|sunscreen|"
                        r"deodorant|toothpaste|razor|makeup|skincare|soap|detergent|cleaning|"
                        r"vitamins|haircare)\b", re.I)

# (category regex, low_cents, high_cents) — broad sanity ranges, never shown as exact prices
_CATEGORY_RANGES = [
    (re.compile(r"\b(shampoo|conditioner|body wash|lotion|deodorant|toothpaste)\b", re.I), 400, 1500),
    (re.compile(r"\b(serum|moisturizer|sunscreen|skincare set)\b", re.I), 800, 3500),
    (re.compile(r"\b(air fryer|blender|coffee maker)\b", re.I), 3000, 15000),
    (re.compile(r"\b(robot vacuum)\b", re.I), 15000, 90000),
    (re.compile(r"\b(headphones|earbuds)\b", re.I), 2500, 35000),
    (re.compile(r"\b(smartwatch|fitness tracker)\b", re.I), 5000, 45000),
]

_RETAILER_REASONS = {
    "Amazon": "Fast compare, watch for coupons",
    "Target": "Often has Circle deals",
    "Walmart": "Usually the low-price floor",
    "CVS": "Good for coupons",
    "Walgreens": "Often has multipacks",
    "Google Shopping": "Compare everything at once",
}


def get_price_context(product: dict) -> dict:
    """product: {title, brand?, category?, product_url?, price?, price_verified?, query_text?}"""
    title = product.get("title") or product.get("query_text") or ""
    query = (product.get("query_text") or title)[:80]
    is_drugstore = bool(_DRUGSTORE.search(title))

    ctx = {
        "price_verified": bool(product.get("price_verified")) and bool(product.get("price")),
        "primary_price_text": f"${product['price']}" if product.get("price") else None,
        "primary_price_cents": parse_price_to_cents(str(product.get("price", ""))) if product.get("price") else None,
        "price_confidence": float(product.get("price_confidence", 0) or 0),
        "price_source": "amazon_parser" if product.get("price") else None,
        "comparable_prices": [],
        "estimated_fair_price_range": None,
        "best_known_option": None,
        "warnings": list(product.get("warnings", []))[:2],
        "search_links": [],
    }

    # comparable prices via cached deterministic Amazon search (never Claude)
    try:
        from ..product_search import search_products
        for r in search_products(query, limit=4):
            if r.get("price_text"):
                ctx["comparable_prices"].append({
                    "retailer": "Amazon", "title": r["title"][:80],
                    "price_text": r["price_text"], "product_url": r["product_url"],
                    "confidence": 0.7, "source": "amazon_search"})
    except Exception as e:
        log.debug("comparables unavailable: %s", e)

    # fair range: from comparables, else category heuristic
    cents = [parse_price_to_cents(c["price_text"]) for c in ctx["comparable_prices"]]
    cents = [c for c in cents if c]
    if cents:
        lo, hi = min(cents), max(cents)
        ctx["estimated_fair_price_range"] = {
            "low_text": f"${lo / 100:.2f}", "high_text": f"${hi / 100:.2f}",
            "basis": "current comparable listings", "confidence": 0.7}
        best = min(ctx["comparable_prices"], key=lambda c: parse_price_to_cents(c["price_text"]) or 1e9)
        ctx["best_known_option"] = {"retailer": "Amazon", "price_text": best["price_text"],
                                    "product_url": best["product_url"],
                                    "reason": "lowest comparable found"}
    else:
        for rx, lo, hi in _CATEGORY_RANGES:
            if rx.search(title):
                ctx["estimated_fair_price_range"] = {
                    "low_text": f"${lo / 100:.0f}", "high_text": f"${hi / 100:.0f}",
                    "basis": "typical category range", "confidence": 0.4}
                break

    # retailer actions — clickable cards, not homework for the user
    retailers = [("Amazon", amazon_search_url(query)),
                 ("Target", target_search_url(query)),
                 ("Walmart", walmart_search_url(query))]
    if is_drugstore:
        retailers += [("CVS", cvs_search_url(query)), ("Walgreens", walgreens_search_url(query))]
    retailers.append(("Google Shopping", google_shopping_search_url(query)))
    ctx["search_links"] = [{"label": f"Check {name}" if name != "Google Shopping" else "Compare all",
                            "retailer": name, "url": url,
                            "reason": _RETAILER_REASONS.get(name, "")}
                           for name, url in retailers]
    return ctx


def summarize_for_prompt(ctx: dict) -> str:
    """Compact price block for Claude — a few lines, never raw data."""
    lines = []
    if ctx["price_verified"] and ctx["primary_price_text"]:
        lines.append(f"Listed price: {ctx['primary_price_text']} (verified)")
    elif ctx["primary_price_text"]:
        lines.append("Listed price is UNVERIFIED. Mention that once at most, then move on.")
    else:
        lines.append("No exact price available. Do not state any specific price.")
    r = ctx.get("estimated_fair_price_range")
    if r:
        lines.append(f"Fair range based on {r['basis']}: {r['low_text']}-{r['high_text']}")
    if ctx["comparable_prices"]:
        comp = ", ".join(f"{c['price_text']}" for c in ctx["comparable_prices"][:3])
        lines.append(f"Comparable Amazon listings right now: {comp}")
    if ctx["best_known_option"]:
        b = ctx["best_known_option"]
        lines.append(f"Best known option: {b['price_text']} at {b['retailer']}")
    stores = ", ".join(s["retailer"] for s in ctx["search_links"][:5])
    lines.append(f"The UI shows clickable check-price cards for: {stores}. "
                 "Refer to them as 'the store cards below', never tell the user to go search manually.")
    return "\n".join(lines)
