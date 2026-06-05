"""Cross-platform creator fingerprint — the identity layer, built from
every connected source. Clearly separates confirmed / sourced / inferred facts."""
import json
import logging

from ..config import PROFILE_DIR
from ..db import get_db, now
from ..llm import complete_json
from ..prompts import FINGERPRINT_SYSTEM, XPLATFORM_FINGERPRINT_PROMPT

log = logging.getLogger("creator_twin.intelligence.fingerprint")


def _gather(creator_id: str):
    with get_db() as db:
        profiles = [dict(r) for r in db.execute(
            "SELECT platform, handle, bio, follower_count, post_count FROM creator_profiles WHERE creator_id=?",
            (creator_id,)).fetchall()]
        items = [dict(r) for r in db.execute(
            """SELECT platform, content_type, title, caption, description, text_body, metrics_json,
                      source_type, selected_for_deep_pass
               FROM content_items WHERE creator_id=?
               ORDER BY selected_for_deep_pass DESC, length(text_body) DESC LIMIT 250""",
            (creator_id,)).fetchall()]
        comments = [dict(r) for r in db.execute(
            "SELECT text, like_count FROM comments WHERE content_id IN "
            "(SELECT content_id FROM content_items WHERE creator_id=?) ORDER BY like_count DESC LIMIT 80",
            (creator_id,)).fetchall()]
        intake = db.execute(
            "SELECT intake_json FROM creator_intake WHERE creator_id=? ORDER BY created_at DESC LIMIT 1",
            (creator_id,)).fetchone()
        uploads = [dict(r) for r in db.execute(
            "SELECT title, text_body FROM content_items WHERE creator_id=? AND source_type='creator_uploaded' LIMIT 10",
            (creator_id,)).fetchall()]
    return profiles, items, comments, intake, uploads


def generate_fingerprint(creator_id: str) -> dict:
    profiles, items, comments, intake, uploads = _gather(creator_id)

    by_platform = {}
    for it in items:
        by_platform.setdefault(it["platform"], []).append(it)
    content_samples = ""
    for platform, its in by_platform.items():
        content_samples += f"\n--- {platform.upper()} ({len(its)} items) ---\n"
        for it in its[:40]:
            label = it["title"] or (it["caption"] or "")[:80] or (it["text_body"] or "")[:80]
            content_samples += f"- [{it['content_type']}] {label[:120]}\n"

    excerpts = "\n\n".join(
        f"--- {it['platform']}: {(it['title'] or '')[:80]} ---\n{it['text_body'][:2000]}"
        for it in items if it["text_body"])[:24000] or "(no real text available — infer from metadata)"

    intake_block = ""
    if intake:
        intake_block += "INTAKE FORM:\n" + intake["intake_json"][:4000] + "\n"
    for u in uploads:
        intake_block += f"UPLOAD '{u['title']}':\n{u['text_body'][:2000]}\n"
    intake_block = intake_block or "(none provided — public-content-only mode)"

    prompt = XPLATFORM_FINGERPRINT_PROMPT.format(
        profiles="\n".join(f"- {p['platform']}: @{p['handle']} | {p['follower_count']:,} followers | "
                           f"{(p['bio'] or '')[:300]}" for p in profiles) or "(none)",
        content_samples=content_samples[:14000],
        excerpts=excerpts,
        comments="\n".join(f"[{c['like_count']} likes] {c['text'][:250]}" for c in comments)[:8000] or "(none)",
        intake=intake_block[:10000])

    profile = complete_json(prompt, system=FINGERPRINT_SYSTEM, max_tokens=8000)

    has_confirmed = bool(intake or uploads)
    has_real_text = any(it["text_body"] for it in items)
    source = "creator_confirmed_plus_ai_inferred" if has_confirmed else "ai_inferred_creator_fingerprint"
    confidence = "high" if (has_confirmed and has_real_text) else ("medium" if has_real_text else "low")

    with get_db() as db:
        db.execute(
            "INSERT INTO creator_fingerprint (creator_id, source_type, confidence, profile_json, approved_by_creator, created_at, updated_at)"
            " VALUES (?,?,?,?,0,?,?)", (creator_id, source, confidence, json.dumps(profile), now(), now()))
    (PROFILE_DIR / f"{creator_id}_fingerprint.json").write_text(json.dumps(profile, indent=2))
    return profile


# re-export helpers used everywhere (single source of truth lives in legacy module)
from ..creator_fingerprint import fingerprint_summary, get_fingerprint  # noqa: E402,F401
