"""Price intelligence + answer style tests.

Run: .venv/bin/python tests/test_price_intel.py
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.intelligence.price_intelligence import get_price_context, summarize_for_prompt
from creator_twin.intelligence.retailer_links import (cvs_search_url, target_search_url,
                                                      walmart_search_url)

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL.append(name)
        print(f"  ✗ {name}")


# retailer links
check("target url", "target.com" in target_search_url("Dove shampoo") and "Dove+shampoo" in target_search_url("Dove shampoo"))
check("walmart url", "walmart.com" in walmart_search_url("Dove shampoo"))
check("cvs url", "cvs.com" in cvs_search_url("Dove shampoo"))

# price context for a drugstore item: retailer links incl CVS/Walgreens, fair range, no Claude
ctx = get_price_context({"title": "Dove Intensive Repair Conditioner", "query_text": "Dove conditioner"})
retailers = [s["retailer"] for s in ctx["search_links"]]
check("drugstore item includes CVS+Walgreens", "CVS" in retailers and "Walgreens" in retailers)
check("always includes Amazon/Target/Walmart", all(r in retailers for r in ("Amazon", "Target", "Walmart")))
check("fair range exists (category heuristic at minimum)", ctx["estimated_fair_price_range"] is not None)
check("unverified price stays unverified", ctx["price_verified"] is False)

# Claude is never used: module imports no llm
src = inspect.getsource(sys.modules["creator_twin.intelligence.price_intelligence"])
check("price intelligence never imports Claude", "from ..llm import" not in src and "complete(" not in src)

# prompt block tells the model about store cards + unverified handling
block = summarize_for_prompt(ctx)
check("prompt block references store cards", "store cards" in block)
check("prompt forbids fake price when none", "Do not state any specific price" in block or "UNVERIFIED" in block)

# style rules in system prompt
from creator_twin.prompts import CHAT_SYSTEM_FIRST_PERSON_TAKE as P
check("style: bans em dashes", "em dash" in P.lower())
check("style: word cap present", "150 WORDS" in P)
check("style: unverified said once", "ONCE" in P)

# em-dash scrub in chat
chat_src = inspect.getsource(sys.modules["creator_twin.chat"].ask) if "creator_twin.chat" in sys.modules else ""
import creator_twin.chat as chat
chat_src = inspect.getsource(chat.ask)
check("answers scrub em dashes", "\\u2014" in chat_src or "—" in chat_src)
check("price chips wired", "Compare prices" in chat_src)
check("retailer_actions returned", "retailer_actions" in chat_src)

print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
