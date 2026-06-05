"""Suspicious listing detection: fake/SEO-spam products, impossible prices."""
import datetime
import re

from ..price_validation import category_floor_cents, parse_price_to_cents

_APPLE_CHIP_MISMATCH = re.compile(r"\bmacbook\b.*\ba\d{2}\b|\ba\d{2}\b.*\bmacbook\b", re.I)
_SPAM_HINTS = re.compile(r"\b(2 in 1 in 1|win11|windows 11 pro key|free gift|original genuine)\b", re.I)


def sanity_check_product(product: dict) -> dict:
    """product: {title, price(text or number), price_verified?}"""
    title = product.get("title", "") or ""
    reasons = []
    severity = "low"

    # future/unreleased model years
    year_now = datetime.date.today().year
    for y in re.findall(r"\b(20\d{2})\b", title):
        if int(y) > year_now:
            reasons.append(f"title claims unreleased {y} model")
            severity = "medium"

    # MacBooks don't ship A-series phone chips
    if _APPLE_CHIP_MISMATCH.search(title):
        reasons.append("Apple laptop with phone-class chip in title — likely fake/SEO listing")
        severity = "high"

    if _SPAM_HINTS.search(title):
        reasons.append("title matches SEO-spam patterns")
        severity = "medium"

    # too-good-to-be-true price for the category
    cents = parse_price_to_cents(str(product.get("price", "")))
    floor = category_floor_cents(title)
    if cents and floor and cents < floor:
        reasons.append(f"price ${cents/100:.0f} far below normal range for this category")
        severity = "high"

    # unknown 'model' words after a major brand (cheap heuristic for fakes like 'MacBook Neo')
    if re.search(r"\bmacbook\s+(?!air|pro\b)[a-z]+", title, re.I):
        reasons.append("unrecognized MacBook model name")
        severity = "high"

    return {"suspicious": bool(reasons), "reasons": reasons,
            "severity": severity if reasons else "low"}
