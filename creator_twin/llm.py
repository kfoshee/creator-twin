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


import os
MAX_INPUT_TOKENS = int(os.environ.get("ANTHROPIC_MAX_INPUT_TOKENS_PER_CALL", 3000))
MAX_OUTPUT_TOKENS = int(os.environ.get("ANTHROPIC_MAX_OUTPUT_TOKENS_PER_CALL", 600))
# rough sonnet pricing for telemetry
_IN_COST, _OUT_COST = 3.0 / 1_000_000, 15.0 / 1_000_000


def _log_usage(task, model, in_tok, out_tok, creator_id=""):
    try:
        from .db import new_id, now
        from .db_writer import write
        cost = round(in_tok * _IN_COST + out_tok * _OUT_COST, 6)
        write(lambda c: c.execute(
            "INSERT INTO llm_usage_logs (id, provider, model, task, creator_id, input_tokens, output_tokens, estimated_cost, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("lu"), "anthropic", model, task, creator_id, in_tok, out_tok, cost, now())))
    except Exception:
        pass


def complete(prompt: str, system: str = "", max_tokens: int = 4000, temperature: float = 0.6,
             task: str = "general", creator_id: str = "") -> str:
    """Single-turn completion against Claude. Routed, budgeted, capped, logged."""
    if not ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set. Add it to .env")
    from .llm_router import allowed
    if not allowed(task):
        raise LLMError(f"LLM call blocked by router for build-time task '{task}'")
    from .intelligence.ai_budget import consume
    consume()  # fast builds cap LLM spend; raises BudgetExhausted when out

    # hard input cap: never ship giant contexts to Claude
    budget_chars = MAX_INPUT_TOKENS * 4
    if len(prompt) + len(system) > budget_chars:
        keep_tail = 1200  # the question/instructions live at the end
        head = budget_chars - len(system) - keep_tail - 60
        log.warning("prompt truncated %d -> %d chars for task %s", len(prompt), head + keep_tail, task)
        prompt = prompt[:max(head, 500)] + "\n...[context truncated]...\n" + prompt[-keep_tail:]
    max_tokens = min(max_tokens, MAX_OUTPUT_TOKENS)
    last_err = None
    for attempt in range(3):
        try:
            out = _anthropic(prompt, system, max_tokens, temperature)
            _log_usage(task, ANTHROPIC_MODEL, (len(prompt) + len(system)) // 4, len(out) // 4, creator_id)
            return out
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


def complete_json(prompt: str, system: str = "", max_tokens: int = 8000, task: str = "general"):
    """Completion that must return parseable JSON. Repairs common failures."""
    text = complete(prompt + "\n\nReturn ONLY valid JSON. No markdown fences, no commentary.",
                    system, max_tokens, temperature=0.4, task=task)
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
