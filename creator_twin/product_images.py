"""Real product images + URLs for suggestions, via Amazon search (cached)."""
import concurrent.futures
import hashlib
import json
import logging
import re

import requests

from .config import CACHE_DIR

log = logging.getLogger("creator_twin.product_images")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def lookup_product(query: str):
    """Return {image, url, title} for a product name, or None. Disk-cached."""
    cache = CACHE_DIR / f"prod_{hashlib.sha1(query.lower().encode()).hexdigest()}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        return data or None
    result = None
    try:
        r = requests.get("https://www.amazon.com/s", params={"k": query}, headers=UA, timeout=10)
        if r.status_code == 200:
            imgs = re.findall(r'class="s-image[^"]*" src="(https://m\.media-amazon\.com[^"]+)"', r.text)
            links = re.findall(r'href="(/[^"]*?/dp/[A-Z0-9]{10})', r.text)
            if imgs:
                result = {"image": imgs[0],
                          "url": ("https://www.amazon.com" + links[0].split("?")[0]) if links else "",
                          "title": query}
    except Exception as e:
        log.debug("product lookup failed for %s: %s", query, e)
    cache.write_text(json.dumps(result))
    return result


def lookup_many(queries: list, workers: int = 4) -> dict:
    """Parallel lookups: {query: result_or_None}."""
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(lookup_product, q): q for q in queries}
        for f in concurrent.futures.as_completed(futs):
            try:
                out[futs[f]] = f.result()
            except Exception:
                out[futs[f]] = None
    return out
