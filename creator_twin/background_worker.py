"""Background enrichment worker.

The fast build makes the twin usable; this keeps processing the remaining
catalog (pending content_items) in batches, then refreshes the index.
Safe to re-run: it only touches processing_status='pending' items.

CLI: python creator_twin/background_worker.py --creator-id CR_ID
"""
import argparse
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.db import get_db
from creator_twin.intelligence.synthetic_catalog import generate_catalog
from creator_twin.rag.chunker import build_chunks

log = logging.getLogger("creator_twin.enrich")

BATCH = 24
_ACTIVE: set = set()  # creator_ids with a live worker (per-process dedupe)


def pending_count(creator_id: str) -> int:
    with get_db() as db:
        return db.execute(
            "SELECT COUNT(*) AS n FROM content_items WHERE creator_id=? AND processing_status='pending'",
            (creator_id,)).fetchone()["n"]


def enrich(creator_id: str, max_batches: int = None) -> int:
    """Process pending items in batches until done. Returns items processed."""
    total, batches = 0, 0
    while True:
        remaining = pending_count(creator_id)
        if remaining == 0 or (max_batches and batches >= max_batches):
            break
        log.info("Enriching %s: %d items remaining", creator_id, remaining)
        try:
            done = generate_catalog(creator_id, scope="pending", batch_limit=BATCH,
                                    progress_label="Background enrichment")
        except Exception as e:
            log.warning("enrichment batch failed: %s — backing off 60s", e)
            time.sleep(60)
            continue
        if done == 0:  # nothing progressed (all failed) — stop to avoid spinning
            break
        total += done
        batches += 1
        build_chunks(creator_id)  # refresh index so new knowledge is queryable
    log.info("Enrichment for %s finished: %d items processed", creator_id, total)
    return total


def start_background_enrichment(creator_id: str):
    """Fire-and-forget worker thread (used by the server after fast build)."""
    if creator_id in _ACTIVE:
        return
    _ACTIVE.add(creator_id)

    def worker():
        try:
            enrich(creator_id)
        finally:
            _ACTIVE.discard(creator_id)

    threading.Thread(target=worker, daemon=True, name=f"enrich-{creator_id}").start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-id", required=True)
    ap.add_argument("--max-batches", type=int)
    args = ap.parse_args()
    n = enrich(args.creator_id, args.max_batches)
    print(f"Processed {n} items; {pending_count(args.creator_id)} still pending")
