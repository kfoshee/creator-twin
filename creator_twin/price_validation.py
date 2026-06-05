"""Price validation: never show a wrong/unverified price as fact.

Buybox-first extraction, context rejection (coupons, monthly payments,
list prices), and category sanity floors (a $139 'MacBook' is not a price,
it's a red flag).
"""
import re

# (title pattern, minimum plausible price in cents)
CATEGORY_FLOORS = [
    (re.compile(r"\b(macbook|laptop|notebook pc|chromebook pro)\b", re.I), 200_00),
    (re.compile(r"\b(iphone|galaxy s\d|pixel \d|smartphone)\b", re.I), 120_00),
    (re.compile(r"\b(ipad|tablet)\b", re.I), 80_00),
    (re.compile(r"\b(oled|qled|\d{2}[\"”]? tv)\b", re.I), 150_00),
    (re.compile(r"\b(dslr|mirrorless|camera body)\b", re.I), 150_00),
    (re.compile(r"\b(robot vacuum|roborock|dreame [xls]\d|roomba)\b", re.I), 100_00),
    (re.compile(r"\b(apple watch|garmin (fenix|epix|venu)|smartwatch ultra)\b", re.I), 80_00),
    (re.compile(r"\b(drone|mavic|dji air)\b", re.I), 100_00),
    (re.compile(r"\b(gpu|rtx \d{4}|graphics card)\b", re.I), 150_00),
]

# price found near these words is NOT the product price
BAD_CONTEXT = re.compile(
    r"(/\s*mo|per month|monthly|/month|save \$|coupon|trade[- ]in|list price|was \$|"
    r"typical price|shipping|delivery|protection plan|warranty|with exchange)", re.I)


def normalize_price_text(text: str) -> str:
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", text or "")
    return f"${m.group(1)}" if m else ""


def parse_price_to_cents(price_text: str):
    m = re.search(r"([\d,]+)(?:\.(\d{1,2}))?", price_text or "")
    if not m:
        return None
    dollars = int(m.group(1).replace(",", ""))
    cents = int((m.group(2) or "0").ljust(2, "0"))
    return dollars * 100 + cents


_ACCESSORY = re.compile(
    r"\b(case|cover|charger|cable|screen protector|protector|band|strap|stand|mount|"
    r"adapter|skin|holder|sleeve|hub|dock|filter|replacement|accessor)", re.I)


def category_floor_cents(title: str):
    if _ACCESSORY.search(title or ""):
        return None  # accessories are legitimately cheap
    for rx, floor in CATEGORY_FLOORS:
        if rx.search(title or ""):
            return floor
    return None


def validate_price_candidate(price_text: str, product_title: str, page_context: str = ""):
    """Returns (ok: bool, reason: str)."""
    cents = parse_price_to_cents(price_text)
    if cents is None or cents < 100:
        return False, "unparseable or sub-$1 price"
    if BAD_CONTEXT.search(page_context or ""):
        return False, "price found in coupon/monthly/list-price context"
    floor = category_floor_cents(product_title)
    if floor and cents < floor:
        return False, f"${cents/100:.2f} is suspiciously low for this product category"
    return True, ""


def choose_best_price(candidates: list, product_title: str):
    """candidates: [(price_text, context, source_rank)] — lower rank = more trusted
    (buybox=0). Returns (price_text, confidence, warnings)."""
    warnings = []
    valid = []
    for price_text, context, rank in candidates:
        ok, reason = validate_price_candidate(price_text, product_title, context)
        if ok:
            valid.append((rank, price_text))
        elif reason:
            warnings.append(reason)
    if not valid:
        return None, 0.0, warnings or ["Price could not be verified."]
    valid.sort()
    rank, best = valid[0]
    confidence = 0.95 if rank == 0 else 0.8 if rank == 1 else 0.6
    return normalize_price_text(best), confidence, warnings
