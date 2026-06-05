"""Fetch lightweight product info from a pasted link (Amazon, Target, anything).

Best-effort: title + description + price from meta tags. Fails silently —
the chat still answers, just without page context.
"""
import html
import logging
import re

import requests

log = logging.getLogger("creator_twin.product")

URL_RE = re.compile(r'https?://[^\s<>"\)]+')
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def extract_urls(text: str) -> list:
    return URL_RE.findall(text or "")[:2]


def _meta(page: str, *names):
    for name in names:
        m = re.search(
            r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']+)' % re.escape(name),
            page, re.IGNORECASE)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def fetch_product_info(url: str):
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                            timeout=10, allow_redirects=True)
        page = resp.text[:400_000]
    except Exception as e:
        log.warning("Product fetch failed for %s: %s", url, e)
        return None

    image = _meta(page, "og:image", "twitter:image")
    if not image:  # Amazon hides og tags; grab the landing image
        m = re.search(r'id="landingImage"[^>]+src="([^"]+)"', page) or \
            re.search(r'"hiRes"\s*:\s*"(https[^"]+)"', page) or \
            re.search(r'"large"\s*:\s*"(https[^"]+\.(?:jpg|png|webp)[^"]*)"', page)
        image = m.group(1) if m else ""

    title = _meta(page, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
        title = html.unescape(m.group(1)).strip()[:200] if m else ""
    desc = _meta(page, "og:description", "twitter:description", "description")

    # buybox-first price extraction with validation — never grab random numbers
    from .price_validation import choose_best_price
    candidates = []
    BUYBOX_IDS = ["corePrice_feature_div", "price_inside_buybox", "apex_desktop",
                  "priceblock_ourprice", "priceblock_dealprice"]
    for rank, bid in enumerate(BUYBOX_IDS):
        idx = page.find(f'id="{bid}"')
        if idx >= 0:
            window = page[idx:idx + 3000]
            for m in re.finditer(r'a-offscreen">\s*(\$[\d,]+\.?\d{0,2})', window):
                ctx = window[max(0, m.start() - 150):m.end() + 150]
                candidates.append((m.group(1), ctx, rank))
    meta_price = _meta(page, "product:price:amount", "og:price:amount")
    if meta_price:
        candidates.append((meta_price, "", 1))
    m = re.search(r'"price"\s*:\s*"?(\$?\d[\d,]*\.?\d{0,2})', page)
    if m:
        candidates.append((m.group(1), page[max(0, m.start() - 150):m.end() + 150], 2))

    if not title:
        return None
    price, price_confidence, warnings = choose_best_price(candidates, title)
    price_verified = bool(price) and price_confidence >= 0.75

    from .intelligence.product_sanity import sanity_check_product
    sanity = sanity_check_product({"title": title, "price": price or ""})
    if sanity["suspicious"]:
        warnings = warnings + sanity["reasons"]
        if sanity["severity"] == "high":
            price_verified = False

    result = {"url": url, "title": title[:200], "description": desc[:400],
              "price": (price or "").lstrip("$")[:20], "image": image[:500],
              "price_verified": price_verified, "price_confidence": round(price_confidence, 2),
              "warnings": warnings[:4], "suspicious": sanity["suspicious"],
              "suspicion_severity": sanity["severity"],
              "metadata_source": "amazon_parser"}
    _persist_resolved(result)
    return result


def _persist_resolved(p: dict):
    """Store verified metadata (resolved_products) via the writer queue."""
    try:
        from .db import new_id, now
        from .db_writer import write
        from .product_search import extract_asin
        write(lambda c: c.execute(
            """INSERT INTO resolved_products
               (product_id, source, input_url, canonical_url, asin, title, price_text,
                image_url, metadata_source, metadata_confidence, price_verified_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id("rp"), "amazon", p["url"], p["url"], extract_asin(p["url"]) or "",
             p["title"], p["price"], p["image"], p["metadata_source"],
             p["price_confidence"], now() if p["price_verified"] else None, now(), now())))
    except Exception as e:
        log.debug("resolved_products persist skipped: %s", e)
