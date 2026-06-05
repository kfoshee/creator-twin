"""Generate the creator identity fingerprint — the heart of the fast build.

Built from channel metadata, playlists, top titles/descriptions, selected
transcripts, top comments, and the creator intake form (if provided).
Stored as source_type='ai_inferred_creator_fingerprint' until approved.
"""
import json
import logging

from .comments_fetch import top_comments_text
from .config import PROFILE_DIR
from .db import get_db, now
from .llm import complete_json
from .prompts import FINGERPRINT_PROMPT, FINGERPRINT_SYSTEM
from .transcript_optional import get_transcript
from .youtube_metadata import fetch_playlists

log = logging.getLogger("creator_twin.fingerprint")


def load_intake(intake_path=None):
    if not intake_path:
        return None
    try:
        with open(intake_path) as f:
            return json.load(f)
    except Exception as e:
        log.warning("Could not load intake file %s: %s", intake_path, e)
        return None


def generate_fingerprint(creator_id: str, intake_path=None) -> dict:
    with get_db() as db:
        creator = dict(db.execute("SELECT * FROM creators WHERE creator_id=?", (creator_id,)).fetchone())
        videos = [dict(r) for r in db.execute(
            "SELECT video_id, title, description, view_count, selected_for_deep_pass FROM videos "
            "WHERE creator_id=? ORDER BY view_count DESC LIMIT 60", (creator_id,)).fetchall()]

    try:
        playlists = fetch_playlists(creator["channel_id"])
    except Exception:
        playlists = []

    # transcript excerpts from up to 8 deep-pass videos
    excerpts = []
    for v in [v for v in videos if v["selected_for_deep_pass"]][:8]:
        t = get_transcript(v["video_id"])
        if t:
            excerpts.append(f"--- {v['title']} ---\n{t[:2500]}")

    intake = load_intake(intake_path)
    prompt = FINGERPRINT_PROMPT.format(
        channel_title=creator["channel_title"],
        channel_description=(creator["channel_description"] or "")[:2000],
        keywords="",
        subscriber_count=creator["subscriber_count"],
        playlists="\n".join(f"- {p['title']} ({p['item_count']} videos): {p['description'][:120]}"
                            for p in playlists) or "(none)",
        videos="\n".join(f"- {v['title']} | {v['view_count']:,} views | {(v['description'] or '')[:200]}"
                         for v in videos),
        transcripts="\n\n".join(excerpts) or "(no transcripts available — infer from metadata)",
        comments=top_comments_text(creator_id, limit=80) or "(no comments fetched)",
        intake=json.dumps(intake, indent=1) if intake else "(no intake form provided — metadata-only mode)",
    )

    profile = complete_json(prompt, system=FINGERPRINT_SYSTEM, max_tokens=8000)
    source = "creator_intake_plus_ai_inferred" if intake else "ai_inferred_creator_fingerprint"
    confidence = "high" if (intake and excerpts) else ("medium" if excerpts else "low")

    with get_db() as db:
        db.execute("INSERT INTO creator_fingerprint (creator_id, source_type, confidence, profile_json, approved_by_creator, created_at, updated_at)"
                   " VALUES (?,?,?,?,0,?,?)",
                   (creator_id, source, confidence, json.dumps(profile), now(), now()))

    out = PROFILE_DIR / f"{creator_id}_fingerprint.json"
    out.write_text(json.dumps(profile, indent=2))
    log.info("Fingerprint saved to %s", out)
    return profile


def get_fingerprint(creator_id: str):
    """Latest fingerprint; approved one wins over inferred."""
    with get_db() as db:
        row = db.execute(
            "SELECT profile_json, source_type, confidence, approved_by_creator FROM creator_fingerprint "
            "WHERE creator_id=? ORDER BY approved_by_creator DESC, id DESC LIMIT 1",
            (creator_id,)).fetchone()
    if not row:
        return None
    profile = json.loads(row["profile_json"])
    profile["_meta"] = {"source_type": row["source_type"], "confidence": row["confidence"],
                        "approved_by_creator": bool(row["approved_by_creator"])}
    return profile


def fingerprint_summary(profile: dict, max_len=1200) -> str:
    if not profile:
        return ""
    parts = [
        f"Identity: {profile.get('one_sentence_identity', '')}",
        f"Audience: {profile.get('target_audience', '')}",
        f"Topics: {', '.join(profile.get('primary_topics', [])[:8])}",
        f"Tone: {profile.get('tone_style', '')}",
        f"Repeated advice: {'; '.join(profile.get('repeated_advice', [])[:5])}",
    ]
    return "\n".join(parts)[:max_len]
