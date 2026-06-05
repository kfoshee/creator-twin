"""Product search expansion + fallback tests.

Run: .venv/bin/python tests/test_search_expand.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.product_search import normalize_product_query, search_suggestions_for

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name}")


check("'dove sha' -> 'dove shampoo'", normalize_product_query("dove sha") == "dove shampoo")
check("'dove sham' -> 'dove shampoo'", normalize_product_query("dove sham") == "dove shampoo")
check("'fitbit char' -> 'fitbit charge'", normalize_product_query("fitbit char") == "fitbit charge")
check("'airf' -> 'air fryer'", normalize_product_query("airf") == "air fryer")
check("full words untouched", normalize_product_query("dove shampoo") == "dove shampoo")

s = search_suggestions_for("dove sha")
titles = [x["title"].lower() for x in s]
check("suggestions returned for 'dove sha'", len(s) >= 3)
check("includes dove shampoo", any("dove" in t and "shampoo" in t for t in titles))
check("all are search_suggestion type", all(x["type"] == "search_suggestion" for x in s))
check("amazon search URLs present", all("amazon.com/s?k=" in x["product_url"] for x in s))
check("no verified price on suggestions", all(not x["price_verified"] for x in s))

s2 = search_suggestions_for("zxqv unknown thing")
check("nonsense still yields the query itself", len(s2) >= 1)

print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
