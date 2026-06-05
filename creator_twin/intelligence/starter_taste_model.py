"""Starter taste model: 2 controlled Gemini calls that actually read the creator.

Call 1 — taste: niche, product world, retailer context, deal logic.
Call 2 — answer guidance: voice/price/retailer rules for final Claude takes.
Cached by creator + content hash; never re-billed on refresh. Claude: never.
"""
import hashlib
import json
import logging

from ..db import get_db, now
from ..db_writer import write
from ..llm_gemini import GeminiError, available, complete_json as gcomplete

log = logging.getLogger("creator_twin.starter_taste")

TASTE_SYSTEM = ("You analyze a creator's actual content to learn their product taste. Be SPECIFIC "
                "to this creator: name real brands, retailers, and categories visible in their "
                "titles. Never give generic filler when titles show specifics.")

GUIDANCE_SYSTEM = ("You write compact answer-style guidance so an AI twin gives product takes the "
                   "way THIS creator would. Short actionable rules only.")


def _gather(creator_id):
    with get_db() as db:
        c = db.execute("SELECT channel_title, channel_description, custom_url FROM creators "
                       "WHERE creator_id=?", (creator_id,)).fetchone()
        items = db.execute(
            """SELECT title, caption, description FROM content_items WHERE creator_id=?
               ORDER BY published_at DESC LIMIT 75""", (creator_id,)).fetchall()
        cands = [p for r in db.execute(
            "SELECT products_mentioned_json FROM content_items WHERE creator_id=? LIMIT 100",
            (creator_id,)).fetchall() for p in json.loads(r[0] or "[]")]
    titles = [(it["title"] or it["caption"] or "")[:120] for it in items if it["title"] or it["caption"]]
    descs = [(it["description"] or "")[:250] for it in items[:10] if it["description"]]
    return ((c["channel_title"] if c else "") or "this creator",
            ((c["channel_description"] if c else "") or "")[:600],
            (c["custom_url"] if c else "") or "",
            titles, descs, list(dict.fromkeys(cands))[:15])


def _content_hash(titles):
    return hashlib.sha1("|".join(titles[:60]).encode()).hexdigest()


def _cache_get(key):
    with get_db() as db:
        row = db.execute("SELECT suggestions_json FROM suggestion_cache WHERE cache_key=?",
                         (key,)).fetchone()
    return json.loads(row["suggestions_json"]) if row else None


def _cache_put(key, payload):
    write(lambda c: c.execute(
        "INSERT OR REPLACE INTO suggestion_cache (cache_key, provider, suggestions_json, created_at)"
        " VALUES (?,?,?,?)", (key, "gemini", json.dumps(payload), now())))


def build_starter_taste_model(creator_id: str):
    """Returns (taste, guidance, source). source: gemini_starter | deterministic_starter."""
    if not available():
        return None, None, "deterministic_starter"
    name, bio, handle, titles, descs, cands = _gather(creator_id)
    chash = _content_hash(titles)
    cached = _cache_get(f"taste:{creator_id}:{chash}")
    if cached:
        log.info("taste model cache hit for %s", creator_id)
        return cached.get("taste"), cached.get("guidance"), "gemini_starter"

    titles_block = "\n".join(f"- {t}" for t in titles[:60])
    try:
        taste = gcomplete(
            f"Creator: {name} ({handle})\nBio: {bio}\n"
            f"Recent video titles ({len(titles)}):\n{titles_block}\n"
            f"Description excerpts:\n" + "\n".join(descs[:6]) +
            f"\nExtracted product candidates: {', '.join(cands) or '(none)'}\n\n"
            'Return JSON: {"creator_niche":"...","audience":"...","product_world":["..."],'
            '"specific_product_categories":["..."],"retailer_context":["retailers/stores they '
            'actually mention"],"creator_buying_criteria":["..."],"deal_logic":["how they judge '
            'deals, e.g. price per unit, coupon stacking"],"style_notes":["..."],'
            '"likely_recommendation_patterns":["..."],"what_to_avoid":["..."],"confidence":0.0}',
            system=TASTE_SYSTEM, task="gemini_starter_taste", creator_id=creator_id)

        guidance = gcomplete(
            f"Creator: {name} | niche: {taste.get('creator_niche', '')}\n"
            f"Product world: {', '.join((taste.get('product_world') or [])[:6])}\n"
            f"Retailers: {', '.join((taste.get('retailer_context') or [])[:5])}\n"
            f"Deal logic: {', '.join((taste.get('deal_logic') or [])[:4])}\n"
            f"Title style examples:\n" + "\n".join(f"- {t}" for t in titles[:12]) +
            '\n\nReturn JSON: {"voice_rules":["..."],"price_rules":["..."],'
            '"creator_specific_take_rules":["..."],"retailer_rules":["..."],'
            '"answer_do":["..."],"answer_dont":["..."]}',
            system=GUIDANCE_SYSTEM, task="gemini_starter_guidance", creator_id=creator_id)
    except GeminiError as e:
        log.warning("gemini taste model failed: %s", e)
        return None, None, "deterministic_starter"

    _cache_put(f"taste:{creator_id}:{chash}", {"taste": taste, "guidance": guidance})
    return taste, guidance, "gemini_starter"
