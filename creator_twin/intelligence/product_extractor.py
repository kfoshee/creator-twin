"""Product mention extraction — deterministic rules first, cheap and generic.

Pulls product names, brands, categories and comparison pairs out of titles,
descriptions, captions, transcripts and comments. Works for any niche; the
brand list is a recall booster, not a requirement (capitalized Model-Number
patterns work for unknown brands too).
"""
import json
import logging
import re

from ..db import get_db

log = logging.getLogger("creator_twin.intelligence.product_extractor")

KNOWN_BRANDS = [
    # wearables / health
    "Fitbit", "Garmin", "Oura", "WHOOP", "Polar", "Withings", "Amazfit", "Coros",
    "Suunto", "Ultrahuman", "Eight Sleep", "Apple Watch", "Galaxy Watch", "Pixel Watch",
    # tech
    "Apple", "Samsung", "Google", "Sony", "Bose", "Anker", "Logitech", "Razer",
    "Corsair", "Keychron", "DJI", "GoPro", "Insta360", "Nvidia", "AMD", "Intel",
    "Dyson", "Shark", "Ninja", "Instant Pot", "KitchenAid", "Vitamix",
    "Roborock", "Dreame", "Eufy", "iRobot", "Roomba", "MacBook", "iPad", "iPhone",
    "ThinkPad", "Surface", "Kindle", "Steam Deck", "Meta Quest", "AirPods",
    "Sonos", "JBL", "Sennheiser", "Shure", "Elgato", "Lumix", "Fujifilm", "Canon", "Nikon",
]
_BRAND_RE = re.compile(r"\b(" + "|".join(re.escape(b) for b in KNOWN_BRANDS) + r")\b", re.IGNORECASE)
# Brand Model 3 / Venu 3 / Charge 6 style: Capitalized words optionally ending in number
_MODEL_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z0-9]+){0,3}\s(?:\d[\w-]*|[A-Z][\w-]*\d[\w-]*|Pro|Max|Ultra|Mini|Air|SE|Plus))\b")
_TRAIL_NOISE = re.compile(r"\s+(review(?:ed)?|unboxing|test(?:ed)?|vs\.?|comparison|setup|hands-on)$", re.IGNORECASE)
_VS_RE = re.compile(r"([A-Z][\w\s-]{2,30}?)\s+(?:vs\.?|versus)\s+([A-Z][\w\s-]{2,30})", re.IGNORECASE)
_NOISE = {"I Tried", "The Best", "My Top", "New Video", "Last Week", "This Year",
          "First Look", "Top 5", "Top 10", "Full Review", "Watch This"}


def extract_from_text(title: str, body: str = "") -> dict:
    """Extract products/brands/comparisons from one piece of content."""
    text = f"{title or ''}\n{(body or '')[:4000]}"
    brands = sorted({m.group(1) for m in _BRAND_RE.finditer(text)},
                    key=lambda b: text.lower().index(b.lower()))
    def clean(name):
        name = re.sub(r"^(?:My|The|This|Our|New|A)\s+", "", name.strip())
        name = _TRAIL_NOISE.sub("", name).strip()
        toks = name.split()
        if len(toks) < 2 or re.fullmatch(r"(?:19|20)\d{2}", toks[-1]):
            return None  # single word or "Something 2026"-style year tail
        if any(t[0].islower() for t in toks):
            return None  # mid-sentence fragments like "apple now requires student"
        return name

    products = []
    for m in _MODEL_RE.finditer(title or ""):
        name = clean(m.group(1))
        if name and name not in _NOISE and len(name) >= 5:
            products.append(name)
    # brand + following capitalized/model tokens ("Fitbit Charge 6 Review" -> "Fitbit Charge 6")
    for b in brands:
        m = re.search(re.escape(b) + r"((?:\s(?:[A-Z][\w-]*|\d[\w-]*)){1,3})", title or "")
        if m:
            cand = clean(b + m.group(1))
            if cand and len(cand) > len(b):
                products.append(cand)
    comparisons = [(a.strip(), b.strip()) for a, b in _VS_RE.findall(title or "")]
    # dedupe, prefer longer names
    seen, deduped = set(), []
    for p in sorted(set(products), key=len, reverse=True):
        key = re.sub(r"\W+", "", p.lower())
        if not any(key in s or s in key for s in seen):
            seen.add(key)
            deduped.append(p)
    return {"products": deduped[:6], "brands": brands[:6], "comparisons": comparisons[:3]}


def extract_creator_products(creator_id: str, limit: int = 300) -> int:
    """Run extraction over the creator's product-related items; store results."""
    with get_db() as db:
        items = [dict(r) for r in db.execute(
            """SELECT content_id, title, caption, description, text_body FROM content_items
               WHERE creator_id=? AND is_product_related=1
               ORDER BY published_at DESC LIMIT ?""", (creator_id, limit)).fetchall()]
    n = 0
    with get_db() as db:
        for it in items:
            res = extract_from_text(it["title"] or it["caption"] or "",
                                    (it["description"] or "") + " " + (it["text_body"] or "")[:2000])
            db.execute(
                "UPDATE content_items SET products_mentioned_json=?, brands_mentioned_json=? WHERE content_id=?",
                (json.dumps(res["products"]), json.dumps(res["brands"]), it["content_id"]))
            if res["products"] or res["brands"]:
                n += 1
    return n
