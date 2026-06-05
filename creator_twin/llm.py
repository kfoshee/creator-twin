"""LLM provider abstraction. Prefers Anthropic; falls back to Gemini.

Both via plain REST so the only dependency is `requests`.
"""
import json
import logging
import re
import time

import requests

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, GEMINI_API_KEY, GEMINI_MODEL

log = logging.getLogger("creator_twin.llm")


class LLMError(Exception):
    pass


def provider() -> str:
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if GEMINI_API_KEY:
        return "gemini"
    return "none"


def complete(prompt: str, system: str = "", max_tokens: int = 4000, temperature: float = 0.6) -> str:
    """Single-turn completion against whichever provider is configured."""
    prov = provider()
    if prov == "none":
        raise LLMError("No LLM API key set. Add ANTHROPIC_API_KEY or GEMINI_API_KEY to .env")
    fn = _anthropic if prov == "anthropic" else _gemini
    last_err = None
    for attempt in range(3):
        try:
            return fn(prompt, system, max_tokens, temperature)
        except LLMError as e:
            last_err = e
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise last_err


def _anthropic(prompt, system, max_tokens, temperature):
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=180)
    if resp.status_code != 200:
        raise LLMError(f"Anthropic {resp.status_code}: {resp.text[:300]}")
    return "".join(b.get("text", "") for b in resp.json().get("content", []))


# fallback chain: free-tier quotas are per-model, so rotate on 429
GEMINI_FALLBACKS = [GEMINI_MODEL, "gemini-2.5-flash", "gemini-2.5-flash-lite",
                    "gemini-2.0-flash-lite", "gemini-2.0-flash"]


def _gemini(prompt, system, max_tokens, temperature):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        # headroom for thinking-model reasoning tokens
        "generationConfig": {"maxOutputTokens": max_tokens + 4096, "temperature": temperature},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    last = None
    for model in dict.fromkeys(GEMINI_FALLBACKS):
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": GEMINI_API_KEY}, json=body, timeout=180)
        if resp.status_code == 429:
            last = LLMError(f"Gemini {model} quota exhausted")
            log.warning("Gemini %s rate-limited, trying next model", model)
            continue
        if resp.status_code != 200:
            raise LLMError(f"Gemini {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise LLMError(f"Gemini empty response: {json.dumps(data)[:300]}")
    raise last or LLMError("Gemini: all models rate-limited")


def complete_json(prompt: str, system: str = "", max_tokens: int = 8000):
    """Completion that must return parseable JSON. Repairs common failures."""
    text = complete(prompt + "\n\nReturn ONLY valid JSON. No markdown fences, no commentary.",
                    system, max_tokens, temperature=0.4)
    return parse_json(text)


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # salvage the largest {...} or [...] block
        for pattern in (r"\{.*\}", r"\[.*\]"):
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
    raise LLMError(f"Could not parse JSON from LLM output: {text[:300]}")
