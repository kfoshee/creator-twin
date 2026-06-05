"""Gemini provider for BUILD-TIME tasks only (cheap, capped, logged).

Claude never runs during builds; Gemini handles the small starter-twin calls.
Input capped ~5k tokens, output ~1.2k. Every call is usage-logged.
"""
import json
import logging
import os
import re

import requests

from .config import ROOT as _ROOT  # noqa: F401  (loads .env first)

log = logging.getLogger("creator_twin.llm_gemini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BUILD_MODEL = os.environ.get("GEMINI_BUILD_MODEL", "gemini-2.5-flash-lite")
USE_GEMINI_FOR_STARTER_TWIN = os.environ.get("USE_GEMINI_FOR_STARTER_TWIN", "true").lower() == "true"
MAX_IN_CHARS = 5000 * 4
MAX_OUT_TOKENS = 1200
_IN_COST, _OUT_COST = 0.075 / 1_000_000, 0.30 / 1_000_000


class GeminiError(Exception):
    pass


def available() -> bool:
    return USE_GEMINI_FOR_STARTER_TWIN and bool(GEMINI_API_KEY)


def complete_json(prompt: str, system: str = "", task: str = "gemini_build",
                  creator_id: str = "", max_tokens: int = MAX_OUT_TOKENS):
    if not GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY not set")
    if len(prompt) > MAX_IN_CHARS:
        prompt = prompt[:MAX_IN_CHARS]
    body = {"contents": [{"role": "user", "parts": [{"text": prompt + "\n\nReturn ONLY valid JSON."}]}],
            "generationConfig": {"maxOutputTokens": min(max_tokens, MAX_OUT_TOKENS) + 1024,
                                 "temperature": 0.3}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    # per-minute rate limits (429) are normal on the free tier mid-build: wait
    # and retry — honest build time, not a fake delay. Daily-quota 429s won't
    # recover, so fail fast and let callers degrade.
    r = None
    for attempt in range(3):
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_BUILD_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY}, json=body, timeout=60)
        if r.status_code != 429:
            break
        if re.search(r"PerDay|per day|daily", r.text or "", re.I):
            raise GeminiError(f"gemini daily quota exhausted: {r.text[:150]}")
        if attempt < 2:
            import time as _t
            wait = 12 * (attempt + 1)
            log.info("gemini 429 — retrying in %ds (%s)", wait, task)
            _t.sleep(wait)
    if r.status_code != 200:
        raise GeminiError(f"gemini {r.status_code}: {r.text[:200]}")
    text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    _log_usage(task, len(prompt) // 4, len(text) // 4, creator_id)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise GeminiError(f"unparseable gemini JSON: {text[:200]}")


def _log_usage(task, in_tok, out_tok, creator_id=""):
    try:
        from .db import new_id, now
        from .db_writer import write
        write(lambda c: c.execute(
            "INSERT INTO llm_usage_logs (id, provider, model, task, creator_id, input_tokens, output_tokens, estimated_cost, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("lg"), "gemini", GEMINI_BUILD_MODEL, task, creator_id, in_tok, out_tok,
             round(in_tok * _IN_COST + out_tok * _OUT_COST, 6), now())))
    except Exception:
        pass
