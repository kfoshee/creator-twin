"""Price verification + sanity acceptance tests.

Run: .venv/bin/python tests/test_price_sanity.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.intelligence.product_sanity import sanity_check_product
from creator_twin.price_validation import (choose_best_price, parse_price_to_cents,
                                           validate_price_candidate)

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name}")


# 1. $139 MacBook = suspicious, not a verified price
ok, reason = validate_price_candidate("$139.00", "Apple 2026 MacBook Neo 13-inch Laptop with A18 Pro chip")
check("$139 MacBook rejected", not ok and "low" in reason)
s = sanity_check_product({"title": "Apple 2026 MacBook Neo 13-inch Laptop with A18 Pro chip", "price": "$139"})
check("fake MacBook flagged suspicious", s["suspicious"] and s["severity"] == "high")
check("flags phone-chip-in-laptop", any("chip" in r for r in s["reasons"]))

# 2/3. multiple candidates: buybox wins, coupon/monthly rejected
price, conf, warn = choose_best_price(
    [("$139.00", "Save $139.00 with coupon", 2),
     ("$24.91", "$24.91/mo for 24 months", 2),
     ("$599.00", "buybox", 0)],
    "Apple MacBook Air 13-inch Laptop")
check("buybox $599 chosen over coupon/monthly", price == "$599.00" and conf >= 0.75)

# low-rank-only candidates → low confidence
price2, conf2, _ = choose_best_price([("$599.00", "", 2)], "MacBook Air Laptop")
check("fallback-source price gets low confidence", price2 and conf2 < 0.75)

# no valid candidates → None + warning
price3, conf3, warn3 = choose_best_price([("$9.99", "", 0)], "Apple MacBook Pro 16 Laptop")
check("no valid price -> None with warning", price3 is None and warn3)

# 4. parse helper
check("parses $1,299.99", parse_price_to_cents("$1,299.99") == 129999)

# 5. legit product passes
ok2, _ = validate_price_candidate("$599.00", "Apple MacBook Air 13-inch Laptop")
s2 = sanity_check_product({"title": "Apple MacBook Air 13-inch M3 Laptop", "price": "$899"})
check("real MacBook at $899 not suspicious", ok2 and not s2["suspicious"])

# 6. prompt rule: unverified price wording present in chat builder
import inspect
from creator_twin import chat
src = inspect.getsource(chat.ask)
check("chat refuses unverified price as fact", "PRICE IS UNVERIFIED" in src and "SUSPICIOUS LISTING" in src)

print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
