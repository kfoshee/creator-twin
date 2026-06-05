"""Actually check retailer prices — never pretend.

Provider priority: SerpAPI Google Shopping (if SERPAPI_KEY) → our live Amazon
search → links_only. The status field tells the UI and the prompt exactly how
much was really checked. No LLMs here.
"""
import logging
import os

from ..db import now
from ..price_validation import parse_price_to_cents
from .retailer_links import (amazon_search_url, cvs_search_url, google_shopping_search_url,
                             target_search_url, walgreens_search_url, walmart_search_url)

log = logging.getLogger("creator_twin.retailer_price_checker")

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
USE_LIVE_PRICE_SEARCH = os.environ.get("USE_LIVE_PRICE_SEARCH", "true").lower() == "true"
PRICE_SEARCH_PROVIDER = os.environ.get("PRICE_SEARCH_PROVIDER",
                                       "serpapi" if SERPAPI_KEY else "amazon")


def _serpapi_shopping(query: str, limit: int = 8) -> list:
    import requests
    r = requests.get("https://serpapi.com/search.json",
                     params={"engine": "google_shopping", "q": query, "api_key": SERPAPI_KEY,
                             "num": limit}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"serpapi {r.status_code}")
    out = []
    for item in r.json().get("shopping_results", [])[:limit]:
        out.append({"retailer": item.get("source", "Unknown"),
                    "title": (item.get("title") or "")[:100],
                    "price_text": item.get("price", ""),
                    "price_cents": parse_price_to_cents(item.get("price", "")),
                    "product_url": item.get("link", ""),
                    "image_url": item.get("thumbnail", ""),
                    "availability": "in_stock", "confidence": 0.85,
                    "source": "serpapi_google_shopping", "notes": ""})
    return out


def _amazon_results(query: str, limit: int = 4) -> list:
    from ..product_search import search_products
    out = []
    for r in search_products(query, limit=limit):
        if r.get("price_text"):
            out.append({"retailer": "Amazon", "title": r["title"][:100],
                        "price_text": r["price_text"],
                        "price_cents": parse_price_to_cents(r["price_text"]),
                        "product_url": r["product_url"], "image_url": r["image_url"],
                        "availability": "in_stock", "confidence": 0.8,
                        "source": "amazon_search", "notes": ""})
    return out


def check_retailer_prices(product: dict) -> dict:
    """Returns checked results when a provider exists; honest links_only otherwise."""
    title = product.get("title") or product.get("query_text") or ""
    query = (product.get("query_text") or title)[:80]
    import re
    is_drugstore = bool(re.search(r"shampoo|conditioner|lotion|skincare|deodorant|toothpaste|"
                                  r"body wash|makeup|soap|detergent", title, re.I))
    links = [{"label": "Amazon", "retailer": "Amazon", "url": amazon_search_url(query),
              "reason": "Fast compare, watch for coupons"},
             {"label": "Target", "retailer": "Target", "url": target_search_url(query),
              "reason": "Often has Circle deals"},
             {"label": "Walmart", "retailer": "Walmart", "url": walmart_search_url(query),
              "reason": "Usually the low-price floor"}]
    if is_drugstore:
        links += [{"label": "CVS", "retailer": "CVS", "url": cvs_search_url(query),
                   "reason": "Good for coupons"},
                  {"label": "Walgreens", "retailer": "Walgreens", "url": walgreens_search_url(query),
                   "reason": "Often has multipacks"}]
    links.append({"label": "Google Shopping", "retailer": "Google Shopping",
                  "url": google_shopping_search_url(query), "reason": "Compare everything at once"})

    results, status = [], "links_only"
    if USE_LIVE_PRICE_SEARCH:
        if PRICE_SEARCH_PROVIDER == "serpapi" and SERPAPI_KEY:
            try:
                results = _serpapi_shopping(query)
                status = "checked"
            except Exception as e:
                log.warning("serpapi failed: %s — falling back to amazon", e)
        if not results:
            try:
                results = _amazon_results(query)
                status = "partial" if results else "links_only"
            except Exception as e:
                log.warning("amazon check failed: %s", e)

    # drop accessory-priced results when the query is a high-ticket category
    from ..price_validation import category_floor_cents
    floor = category_floor_cents(title) or category_floor_cents(query)
    if floor:
        results = [r for r in results
                   if not r.get("price_cents") or r["price_cents"] >= floor * 0.5]

    priced = [r for r in results if r.get("price_cents")]
    best = min(priced, key=lambda r: r["price_cents"]) if priced else None
    price_range = None
    if priced:
        lo, hi = min(p["price_cents"] for p in priced), max(p["price_cents"] for p in priced)
        price_range = {"low_text": f"${lo / 100:.2f}", "high_text": f"${hi / 100:.2f}"}
    return {
        "checked_at": now(), "query_used": query, "status": status,
        "results": results[:8],
        "best_price": ({"retailer": best["retailer"], "price_text": best["price_text"],
                        "product_url": best["product_url"]} if best else None),
        "price_range": price_range,
        "warnings": [] if results or status == "links_only" else ["price check unavailable"],
        "search_links": links,
    }
