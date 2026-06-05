"""Smoke tests: the questions every usable creator twin must answer."""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.chat import ask
from creator_twin.db import get_db

QUESTIONS = [
    "Who is this creator and what do they help people with?",
    "What are their main content pillars across platforms?",
    "How is their Instagram style different from their YouTube style?",
    "What should a beginner watch or read first?",
    "What are common questions their audience asks?",
    "Draft an Instagram caption in their style.",
    "Draft a TikTok hook in their style.",
    "Draft an X thread in their style.",
    "Recommend their best content about their most popular topic.",
    "Which of your answers are based on real sources versus synthetic or inferred notes?",
    "What should the assistant avoid saying?",
]


def main():
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-id", required=True)
    ap.add_argument("--persona-mode", default="companion",
                    choices=["companion", "first_person_draft", "strict_cited_answer"])
    args = ap.parse_args()

    with get_db() as db:
        creator = db.execute("SELECT channel_title FROM creators WHERE creator_id=?",
                             (args.creator_id,)).fetchone()
    if not creator:
        raise SystemExit(f"Unknown creator_id: {args.creator_id}")
    print(f"\n████ SMOKE TEST — {creator['channel_title']} ({args.persona_mode}) ████\n")

    passed = 0
    for i, q in enumerate(QUESTIONS, 1):
        print(f"{'─' * 70}\nQ{i}: {q}\n")
        try:
            r = ask(args.creator_id, q, persona_mode=args.persona_mode)
            print(r["answer"])
            print("\n  Supporting chunks:")
            for s in r["sources"][:4]:
                label = s["title"] or s["chunk_type"]
                print(f"   • [{s['source_type']} | {s['confidence']}] {label[:60]} — {s['excerpt'][:80]}...")
            passed += 1
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
        print()
    print(f"{'═' * 70}\n  {passed}/{len(QUESTIONS)} smoke tests answered\n")


if __name__ == "__main__":
    main()
