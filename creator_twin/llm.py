"""LLM provider: Anthropic (Claude) via plain REST — the only dependency is `requests`."""
import json
import logging
import re
import time

import requests

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

log = logging.getLogger("creator_twin.llm")


class LLMError(Exception):
    pass


def provider() -> str:
    return "anthropic" if ANTHROPIC_API_KEY else "none"


def complete(prompt: str, system: str = "", max_tokens: int = 4000, temperature: float = 0.6) -> str:
    """Single-turn completion against Claude, with retry on transient errors."""
    if not ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env")
    from .intelligence.ai_budget import consume
    consume()  # fast builds cap LLM spend; raises BudgetExhausted when out
    last_err = None
    for attempt in range(3):
        try:
            return _anthropic(prompt, system, max_tokens, temperature)
        except LLMError as e:
            last_err = e
            if attempt < 2 and ("429" in str(e) or "529" in str(e) or "overloaded" in str(e).lower()):
                time.sleep(3 * (attempt + 1))
            elif attempt < 2:
                time.sleep(1.5)
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
        for pattern in (r"\{.*\}", r"\[.*\]"):
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
    raise LLMError(f"Could not parse JSON from LLM output: {text[:300]}")
