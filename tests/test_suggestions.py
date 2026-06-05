"""Suggested-products acceptance tests (plain python, no pytest needed).

Run: .venv/bin/python tests/test_suggestions.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.chat import SYSTEMS
from creator_twin.config import DEFAULT_PERSONA_MODE
from creator_twin.db import get_db
from creator_twin.intelligence.product_extractor import extract_from_text
from creator_twin.intelligence.suggested_products import get_suggested_products

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name}")


# 1. extraction from product-review titles
r1 = extract_from_text("Fitbit Charge 6 Review - Worth It?", "Affiliate links below")
check("extracts product from review title", any("Charge 6" in p for p in r1["products"]))
check("extracts brand", "Fitbit" in r1["brands"])
r2 = extract_from_text("Garmin Venu 3 vs Apple Watch Series 9 - Which to buy?")
check("extracts comparison pair", len(r2["comparisons"]) >= 1)

# 2. suggestions endpoint logic returns >=3 for a product-review creator
with get_db() as db:
    row = db.execute(
        """SELECT creator_id, COUNT(*) AS n FROM content_items WHERE is_product_related=1
           GROUP BY creator_id ORDER BY n DESC LIMIT 1""").fetchone()
if row:
    suggs = get_suggested_products(row["creator_id"], limit=8)
    check("at least 3 suggestions for product creator", len(suggs) >= 3)
    # 4. category prompts exist when exact products are thin (or exact products dominate)
    types = {s["suggestion_type"] for s in suggs}
    check("suggestion types populated", bool(types & {"exact_product", "inferred_product",
                                                      "category_prompt", "comparison_prompt"}))
    check("suggestions have reason labels", all(s["reason_label"] for s in suggs))
else:
    print("  - skipped live suggestion checks (no creator in DB)")

# 5. first_person_creator_take is the default persona
check("default persona is first_person_creator_take", DEFAULT_PERSONA_MODE == "first_person_creator_take")
check("first-person system prompt exists", "first_person_creator_take" in SYSTEMS)
check("prompt forbids third-person reporting", "third person" in SYSTEMS["first_person_creator_take"])
check("prompt forbids false testing claims", "firsthand testing" in SYSTEMS["first_person_creator_take"])

print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
