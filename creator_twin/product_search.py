"""Amazon product search for the search-box experience.

Parses the public search page (same UA that works for product pages),
disk-cached per query. All requests go through the backend — no keys or
affiliate tags in the frontend.
"""
import hashlib
import html as html_mod
import json
import logging
import re

import requests

from .config import CACHE_DIR

log = logging.getLogger("creator_twin.product_search")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}

AMAZON_URL_RE = re.compile(r"(?:amazon\.[a-z.]+|amzn\.to|a\.co)/", re.IGNORECASE)
ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def is_amazon_url(s: str) -> bool:
    return bool(AMAZON_URL_RE.search(s or ""))


def extract_asin(url: str):
    m = ASIN_RE.search(url or "")
    return m.group(1) if m else None


def search_products(query: str, limit: int = 6) -> list:
    query = (query or "").strip()[:120]
    if not query:
        return []
    cache = CACHE_DIR / f"psearch_{hashlib.sha1(query.lower().encode()).hexdigest()}.json"
    if cache.exists():
        return json.loads(cache.read_text())[:limit]
    results = []
    try:
        r = requests.get("https://www.amazon.com/s", params={"k": query}, headers=UA, timeout=10)
        if r.status_code == 200:
            results = _parse_search(r.text, limit=10)
    except Exception as e:
        log.warning("amazon search failed for %r: %s", query, e)
    cache.write_text(json.dumps(results))
    return results[:limit]


def _parse_search(page: str, limit: int = 10) -> list:
    out, seen = [], set()
    parts = re.split(r'data-asin="([A-Z0-9]{10})"', page)
    for asin, block in zip(parts[1::2], parts[2::2]):
        if asin in seen:
            continue
        tag = re.search(r'<img[^>]*class="s-image[^>]*>', block)
        if not tag:
            continue
        img = re.search(r'src="(https://m\.media-amazon\.com[^"]+)"', tag.group(0))
        alt = re.search(r'alt="([^"]*)"', tag.group(0))
        if not img:
            continue
        title = html_mod.unescape(alt.group(1)).strip() if alt else ""
        if not title or title.lower().startswith("sponsored"):
            continue
        price = re.search(r'class="a-offscreen">(\$[\d,]+\.?\d*)', block)
        rating = re.search(r'([\d.]+) out of 5 stars', block)
        reviews = re.search(r'aria-label="([\d,]+)\s*(?:ratings?|reviews?)"', block) or \
            re.search(r'class="a-size-base s-underline-text">([\d,]+)<', block)
        seen.add(asin)
        out.append({
            "product_id": asin, "asin": asin,
            "title": title[:200], "brand": title.split()[0],
            "price_text": price.group(1) if price else "",
            "image_url": img.group(1),
            "product_url": f"https://www.amazon.com/dp/{asin}",
            "rating": float(rating.group(1)) if rating else None,
            "review_count": int(reviews.group(1).replace(",", "")) if reviews else None,
            "source": "amazon", "metadata": {},
        })
        if len(out) >= limit:
            break
    return out


def resolve_input(user_input: str) -> dict:
    """Amazon URL -> parsed product; query -> search results."""
    s = (user_input or "").strip()
    if is_amazon_url(s):
        from .product_lookup import fetch_product_info
        info = fetch_product_info(s)
        if info:
            return {"type": "amazon_url", "results": [{
                "product_id": extract_asin(s) or "", "asin": extract_asin(s) or "",
                "title": info["title"], "brand": info["title"].split()[0],
                "price_text": f"${info['price']}" if info.get("price") else "",
                "image_url": info.get("image", ""), "product_url": s,
                "rating": None, "review_count": None, "source": "amazon", "metadata": {},
            }]}
        return {"type": "amazon_url", "results": []}
    return {"type": "product_query", "results": search_products(s)}
