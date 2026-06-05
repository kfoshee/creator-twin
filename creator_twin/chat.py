"""Chat with a synthetic creator twin: session-aware product advisor.

Sessions persist the current product + conversation, so follow-ups like
"what's a good price?" resolve against the product under discussion.
Weak verdicts trigger alternative suggestions; relevant creator content
is surfaced when it genuinely relates.
"""
import json
import logging
import re

from .config import DEFAULT_PERSONA_MODE, PERSONA_MODES
from .creator_fingerprint import get_fingerprint
from .db import get_db, new_id, now
from .llm import complete
from .product_lookup import extract_urls, fetch_product_info
from .prompts import (CHAT_SYSTEM_COMPANION, CHAT_SYSTEM_FIRST_PERSON,
                      CHAT_SYSTEM_FIRST_PERSON_TAKE, CHAT_SYSTEM_STRICT)
from .rag.citations import context_block, format_sources
from .rag.retriever import search

log = logging.getLogger("creator_twin.chat")

SYSTEMS = {
    "first_person_creator_take": CHAT_SYSTEM_FIRST_PERSON_TAKE,
    "companion": CHAT_SYSTEM_COMPANION,
    "first_person_draft": CHAT_SYSTEM_FIRST_PERSON,
    "strict_cited_answer": CHAT_SYSTEM_STRICT,
}

WEAK_VERDICT = re.compile(r"\b(pass|cautious|caution|overpriced|wait|not worth|skip|wouldn'?t buy|hold off)\b",
                          re.IGNORECASE)


# ---- session helpers ----

def _load_session(creator_id: str, session_id: str = None):
    with get_db() as db:
        if session_id:
            row = db.execute("SELECT * FROM product_take_sessions WHERE session_id=?",
                             (session_id,)).fetchone()
            if row:
                return row["session_id"], json.loads(row["context_json"] or "{}")
        session_id = new_id("ses")
        db.execute(
            "INSERT INTO product_take_sessions (session_id, creator_id, created_at, updated_at)"
            " VALUES (?,?,?,?)", (session_id, creator_id, now(), now()))
    return session_id, {}


def _save_session(session_id: str, ctx: dict):
    with get_db() as db:
        db.execute(
            "UPDATE product_take_sessions SET context_json=?, current_product_json=?, current_topic=?, updated_at=? WHERE session_id=?",
            (json.dumps(ctx)[:20000], json.dumps(ctx.get("current_product") or {}),
             ctx.get("current_topic", ""), now(), session_id))


def _add_message(session_id: str, role: str, content: str, meta: dict = None):
    with get_db() as db:
        db.execute(
            "INSERT INTO product_take_messages (message_id, session_id, role, content, metadata_json, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (new_id("msg"), session_id, role, content[:8000], json.dumps(meta or {})[:4000], now()))


def _session_history(session_id: str, limit: int = 10):
    with get_db() as db:
        rows = db.execute(
            "SELECT role, content FROM product_take_messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _category_of(title: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]{3,}", title or "")
             if w.lower() not in ("the", "with", "for", "and", "pack", "new")]
    return " ".join(words[-3:]).lower() if words else ""


def _clean_answer(text: str):
    """Strip pipe-separated suggestion lines and dangling separators from prose.
    Returns (clean_text, salvaged_followups)."""
    salvaged = []
    lines = (text or "").strip().splitlines()
    while lines:
        last = lines[-1].strip()
        # a short final line of pipe-separated questions is a leaked suggestion row
        if "|" in last and len(last) < 240 and ("?" in last or last.count("|") >= 2):
            parts = [p.strip(" -•·") for p in last.split("|") if p.strip(" -•·")]
            if parts and all(len(p) < 90 for p in parts):
                salvaged = parts + salvaged
                lines.pop()
                continue
        break
    clean = "\n".join(lines)
    clean = re.sub(r"[ \t]*\|[ \t]*", ", ", clean)        # stray inline pipes
    clean = re.sub(r"[,;]\s*$", ".", clean.strip())       # dangling punctuation
    return clean.strip(), salvaged


# ---- main entry ----

def ask(creator_id: str, question: str, persona_mode: str = DEFAULT_PERSONA_MODE,
        history: list = None, k: int = 12, session_id: str = None) -> dict:
    if persona_mode not in PERSONA_MODES:
        persona_mode = DEFAULT_PERSONA_MODE
    with get_db() as db:
        creator = db.execute("SELECT channel_title FROM creators WHERE creator_id=?",
                             (creator_id,)).fetchone()
    creator_name = (creator["channel_title"] if creator else None) or "this creator"

    session_id, ctx = _load_session(creator_id, session_id)
    convo_history = _session_history(session_id) or (history or [])

    # new product link? becomes the session's current product
    products = []
    for url in extract_urls(question):
        info = fetch_product_info(url)
        if info:
            if not info.get("image"):
                try:
                    from .product_images import lookup_product
                    hit = lookup_product(info["title"][:80])
                    if hit:
                        info["image"] = hit["image"]
                except Exception:
                    pass
            products.append(info)
    if products:
        p = products[0]
        ctx["current_product"] = {"title": p["title"], "price": p.get("price", ""),
                                  "url": p["url"], "image": p.get("image", "")}
        ctx["current_product_category"] = _category_of(p["title"])
        ctx["current_topic"] = ctx["current_product_category"]
        ctx.setdefault("mentioned_products", []).append(p["title"][:80])
        ctx["alternatives_suggested"] = False

    current = ctx.get("current_product") or {}

    # retrieval with query expansion: follow-ups inherit the product's terms
    search_query = question
    for p in products:
        search_query += " " + p["title"]
    if not products and current.get("title"):
        search_query += " " + current["title"] + " " + ctx.get("current_product_category", "")
    chunks = search(creator_id, search_query, k=k)
    context = context_block(chunks)

    profile = get_fingerprint(creator_id) or {}
    fp = json.dumps({k_: v for k_, v in profile.items() if k_ != "_meta"}, indent=1)[:5000]
    system = SYSTEMS[persona_mode].format(creator_name=creator_name, fingerprint=fp)

    product_block = ""
    if products:
        lines = []
        for p in products:
            line = f"- {p['title']}"
            if p.get("price") and p.get("price_verified"):
                line += f" — ${p['price']} (verified price)"
            elif p.get("price"):
                line += (f" — listed at ${p['price']} BUT PRICE IS UNVERIFIED: do not state this "
                         "price as fact; say you can't verify the current price from the listing.")
            else:
                line += " — PRICE UNAVAILABLE: say you can't verify the current price."
            if p.get("suspicious"):
                line += ("\n  SUSPICIOUS LISTING (" + "; ".join(p.get("warnings", [])[:2]) +
                         "): advise verifying the listing is genuine before judging value.")
            if p.get("description"):
                line += f"\n  {p['description']}"
            lines.append(line)
        product_block = "\nPRODUCT THE FAN IS ASKING ABOUT:\n" + "\n".join(lines) + "\n"

    ctx_block = ""
    if current.get("title"):
        ctx_block = (f"\nCONVERSATION CONTEXT:\n- Current product under discussion: {current['title']}"
                     + (f" (${current['price']})" if current.get('price') else "")
                     + (f"\n- Category: {ctx.get('current_product_category', '')}")
                     + (f"\n- My last verdict: {ctx.get('last_verdict', 'none yet')}")
                     + (f"\n- Other products mentioned: {', '.join(ctx.get('mentioned_products', [])[-4:])}\n"))

    convo = ""
    for turn in convo_history[-8:]:
        convo += f"\n{turn.get('role', 'user').upper()}: {turn.get('content', '')[:500]}"

    prompt = f"""CONTEXT CHUNKS:
{context}
{product_block}{ctx_block}{f'RECENT CONVERSATION:{convo}' if convo else ''}

FAN QUESTION: {question}

Answer using the context chunks and conversation context. If a product is included, give the creator's likely take on it based on what they recommend and dislike.

Your answer must be clean prose only — NEVER put follow-up suggestions inline in the answer and
NEVER use vertical bars (|), bullets, or separator characters inside the answer text.

After your answer, on its own final line, write exactly:
FOLLOWUPS: <short follow-up question 1> | <short follow-up question 2> | <short follow-up question 3>
These are questions the fan would naturally ask you next (max 7 words each). They appear as UI
buttons, never as part of your prose."""

    raw = complete(prompt, system=system, max_tokens=800, temperature=0.7)
    answer, follow_ups = raw, []
    if "FOLLOWUPS:" in raw:
        answer, _, tail = raw.rpartition("FOLLOWUPS:")
        follow_ups = [q.strip() for q in tail.split("|") if q.strip()][:3]
    answer, salvaged = _clean_answer(answer)
    if salvaged and not follow_ups:
        follow_ups = salvaged[:3]

    # weak verdict -> surface better options
    alternatives = []
    weak = bool(WEAK_VERDICT.search(answer[:300]))
    if weak:
        ctx["last_verdict"] = "weak"
        if current.get("title") and not ctx.get("alternatives_suggested"):
            try:
                from .intelligence.alternative_products import get_alternatives_for_product
                alternatives = get_alternatives_for_product(
                    creator_id, current["title"], ctx.get("current_product_category", ""))
                ctx["alternatives_suggested"] = True
            except Exception as e:
                log.warning("alternatives failed: %s", e)
        follow_ups = (["Show better options", "What price is fair?"]
                      + [f for f in follow_ups if "option" not in f.lower()])[:3]
    elif answer.lower().startswith("my take"):
        ctx["last_verdict"] = "positive"

    # relevant creator content (top sourced chunk with a real URL)
    relevant = []
    for c in chunks:
        meta = c.get("metadata", {})
        if meta.get("url") and meta.get("title") and c["chunk_type"] in ("content_notes", "real_text_chunk"):
            relevant.append({"title": meta["title"], "url": meta["url"],
                             "platform": c.get("platform") or "youtube"})
            break

    _add_message(session_id, "user", question)
    _add_message(session_id, "assistant", answer, {"weak_verdict": weak})
    _save_session(session_id, ctx)

    return {
        "answer": answer,
        "session_id": session_id,
        "follow_ups": follow_ups,
        "suggested_followups": follow_ups,
        "alternative_products": alternatives,
        "relevant_creator_content": relevant,
        "products": products,
        "persona_mode": persona_mode,
        "confidence": "high" if any(c["source_type"] == "real_transcript" for c in chunks[:4]) else "medium",
        "sources": format_sources(chunks),
    }
