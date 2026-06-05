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
    from creator_twin.intelligence.suggested_products import VALID_TYPES
    types = {s["suggestion_type"] for s in suggs}
    check("suggestion types populated", bool(types) and types <= set(VALID_TYPES))
    check("suggestions have reason labels", all(s["reason_label"] for s in suggs))
else:
    print("  - skipped live suggestion checks (no creator in DB)")

# 5. first_person_creator_take is the default persona
check("default persona is first_person_creator_take", DEFAULT_PERSONA_MODE == "first_person_creator_take")
check("first-person system prompt exists", "first_person_creator_take" in SYSTEMS)
check("prompt forbids third-person reporting", "third person" in SYSTEMS["first_person_creator_take"])
check("prompt forbids false testing claims", "firsthand testing" in SYSTEMS["first_person_creator_take"])

# --- suggestion validation (junk like "Comment 3" / "June 2nd") ---
from creator_twin.intelligence.suggested_products import (get_fallback_suggestions_for_creator,
                                                          is_valid_product_suggestion)
check("'Comment 3' rejected", not is_valid_product_suggestion("Comment 3"))
check("'comment 2' rejected", not is_valid_product_suggestion("comment 2"))
check("'June 2nd' rejected", not is_valid_product_suggestion("June 2nd"))
check("'Part 2' rejected", not is_valid_product_suggestion("Part 2"))
check("'Robot vacuum deal' accepted", is_valid_product_suggestion("Robot vacuum deal"))
check("'Air fryer deal' accepted", is_valid_product_suggestion("Air fryer deal"))
check("'MacBook Pro' accepted", is_valid_product_suggestion("MacBook Pro"))
fb = get_fallback_suggestions_for_creator({"one_sentence_identity": "Daily Amazon deals and coupon finds"})
check("deal creator gets deal prompts", any("deal" in f["product_name"].lower() for f in fb)
      and all(f["suggestion_type"] == "deal_prompt" for f in fb))
fb2 = get_fallback_suggestions_for_creator({"primary_content_pillars": ["wearable accuracy", "sleep tracking"]})
check("wearable creator gets wearable prompts", any("tracker" in f["product_name"].lower() for f in fb2))

print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
