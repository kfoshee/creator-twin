"""Per-platform summaries: what each platform does in the creator's strategy."""
import json
import logging

from ..db import get_db, new_id, now
from ..llm import LLMError, complete_json
from ..prompts import PLATFORM_SUMMARY_PROMPT

log = logging.getLogger("creator_twin.intelligence.pillars")


def generate_platform_summaries(creator_id: str, progress=None) -> int:
    with get_db() as db:
        platforms = [r["platform"] for r in db.execute(
            "SELECT DISTINCT platform FROM content_items WHERE creator_id=?", (creator_id,)).fetchall()]
    count = 0
    for platform in platforms:
        with get_db() as db:
            prof = db.execute(
                "SELECT handle, bio, follower_count, post_count FROM creator_profiles "
                "WHERE creator_id=? AND platform=?", (creator_id, platform)).fetchone()
            items = db.execute(
                """SELECT content_type, title, caption, text_body, metrics_json FROM content_items
                   WHERE creator_id=? AND platform=? ORDER BY selected_for_deep_pass DESC LIMIT 40""",
                (creator_id, platform)).fetchall()
        if not items:
            continue
        content = "\n".join(
            f"- [{i['content_type']}] {(i['title'] or i['caption'] or i['text_body'] or '')[:130]} | "
            f"{i['metrics_json'][:80]} | {(i['text_body'] or '')[:150]}"
            for i in items)
        try:
            summary = complete_json(PLATFORM_SUMMARY_PROMPT.format(
                platform=platform,
                profile=json.dumps(dict(prof)) if prof else "(none)",
                content=content[:10000]), max_tokens=3000)
        except LLMError as e:
            log.warning("platform summary %s failed: %s", platform, e)
            continue
        with get_db() as db:
            db.execute("DELETE FROM platform_summaries WHERE creator_id=? AND platform=?",
                       (creator_id, platform))
            db.execute(
                "INSERT INTO platform_summaries (summary_id, creator_id, platform, summary_json, confidence, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (new_id("ps"), creator_id, platform, json.dumps(summary), "medium", now(), now()))
        count += 1
        if progress:
            progress(f"Summarized {platform}")
    return count
