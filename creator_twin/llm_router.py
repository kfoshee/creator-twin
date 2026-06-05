"""Task-based model routing: Claude is for user-facing answers ONLY.

Build-time tasks route to 'none' (deterministic) by default. A blocked task
raises LLMError, which every build-time caller already catches and degrades
gracefully — so builds proceed with zero paid AI.

(Implemented as llm_router.py because creator_twin/llm.py already exists.)
"""
import logging
import os

log = logging.getLogger("creator_twin.llm_router")

# task -> provider ("anthropic" | "none")
ROUTES = {
    # build-time: deterministic only, never Claude
    "product_relevance": "none",
    "product_extraction": "none",
    "suggested_products": "none",
    "content_summary": "none",
    "transcript_summary": "none",
    "starter_fingerprint": "none",
    "platform_summary": "none",
    "qa_generation": "none",
    "review_packet": "none",
    # user-facing: Claude
    "final_product_take": "anthropic",
    "followup_chat": "anthropic",
    # manual enrichment ("Improve this twin") only
    "creator_style_synthesis": "anthropic",
    "deep_fingerprint": "anthropic",
    "deep_summary": "anthropic",
    "general": "anthropic",
}

# Optional escape hatch for bulk work on a cheap provider (off by default; not wired).
USE_CHEAP_FOR_BULK = os.environ.get("USE_GEMINI_FOR_BULK", "false").lower() == "true"


def provider_for(task: str) -> str:
    return ROUTES.get(task, "anthropic")


def allowed(task: str) -> bool:
    p = provider_for(task)
    if p == "none":
        log.info("LLM blocked by router for build-time task %r (deterministic path used)", task)
        return False
    return True
