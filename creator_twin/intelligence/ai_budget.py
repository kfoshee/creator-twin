"""Per-build AI budget: hard cap on LLM calls so fast builds stay cheap.

fast_build installs a budget for its thread; llm.complete() consumes from it.
When exhausted, BudgetExhausted (an LLMError) is raised — existing handlers
mark items pending and the build still becomes usable.
"""
import logging
import threading

from ..llm import LLMError

log = logging.getLogger("creator_twin.ai_budget")

_local = threading.local()


class BudgetExhausted(LLMError):
    pass


class AIBudget:
    def __init__(self, max_llm_calls=10, max_items_to_summarize=10,
                 max_transcripts=5, comments_enabled=False):
        self.max_llm_calls = max_llm_calls
        self.max_items_to_summarize = max_items_to_summarize
        self.max_transcripts = max_transcripts
        self.comments_enabled = comments_enabled
        self.used = 0

    @property
    def remaining(self):
        return None if self.max_llm_calls is None else max(0, self.max_llm_calls - self.used)

    def consume(self, n=1):
        if self.max_llm_calls is not None and self.used + n > self.max_llm_calls:
            raise BudgetExhausted(
                f"AI budget exhausted ({self.used}/{self.max_llm_calls} calls) — "
                "remaining items stay pending until the user asks to improve the twin")
        self.used += n


def install(budget: AIBudget):
    _local.budget = budget


def clear():
    _local.budget = None


def current() -> AIBudget:
    return getattr(_local, "budget", None)


def consume(n=1):
    b = current()
    if b is not None:
        b.consume(n)


def used() -> int:
    b = current()
    return b.used if b else 0
