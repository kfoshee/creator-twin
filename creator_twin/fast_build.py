"""Creator Twin Fast Build — multi-platform. Usable twin in under 60 minutes.

Principles:
- No connector blocks the build. Instagram failing never stops YouTube.
- Transcripts/media are optional enrichment, never the foundation.
- Every fact is labeled: creator-confirmed / sourced / inferred / synthetic.

Examples:
  python creator_twin/fast_build.py --youtube-channel-url "https://youtube.com/@example"
  python creator_twin/fast_build.py --youtube-channel-url "..." --instagram-handle ex \
      --tiktok-handle ex --x-handle ex --website-url "https://example.com" \
      --intake-file creator_intake.json --max-items-per-platform 100 --deep-pass-per-platform 25
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.config import (DEEP_PASS_PER_PLATFORM, MAX_ITEMS_PER_PLATFORM, PROFILE_DIR)
from creator_twin.connectors import get_connector
from creator_twin.db import add_run_error, get_db, new_id, now, update_run
from creator_twin.intelligence.audience_model import generate_audience_model
from creator_twin.intelligence.commerce_intelligence import generate_commerce_intelligence
from creator_twin.intelligence.content_pillars import generate_platform_summaries
from creator_twin.intelligence.creator_fingerprint import generate_fingerprint
from creator_twin.intelligence.style_model import generate_style_model
from creator_twin.intelligence.synthetic_catalog import generate_catalog, generate_qa_pairs
from creator_twin.rag.chunker import build_chunks
from creator_twin.rag.embedder import index_pending
from creator_twin.review.review_packet import generate_review_packet

log = logging.getLogger("creator_twin.build")

STEPS = [
    ("sources", "Finding creator sources"),
    ("catalogscan", "Scanning catalog"),
    ("products", "Finding product content"),
    ("select", "Picking high-signal content"),
    ("fingerprint", "Building first taste model"),
    ("catalog", "Learning their product takes"),
    ("suggestions", "Creating starter suggestions"),
    ("index", "Indexing first answers"),
    ("ready", "Ready"),
]

FIRST_BATCH = 24  # items summarized before the twin is declared usable

MODES = ("preview", "product", "full")


class BuildCancelled(Exception):
    pass


def _ensure_creator(creator_id, sources, intake_file):
    """Create or load the creator row; store intake if provided."""
    with get_db() as db:
        if creator_id:
            row = db.execute("SELECT creator_id FROM creators WHERE creator_id=?", (creator_id,)).fetchone()
            if not row:
                raise SystemExit(f"Unknown --creator-id {creator_id}")
        else:
            creator_id = new_id("cr")
            primary = next(iter(sources)) if sources else "youtube"
            handle = (sources.get(primary) or {}).get("handle") or \
                     (sources.get(primary) or {}).get("channel_url", "")
            db.execute(
                "INSERT INTO creators (creator_id, primary_platform, primary_handle, website_url, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (creator_id, primary, str(handle)[:200],
                 (sources.get("website") or {}).get("website_url", ""), now(), now()))
        if intake_file and Path(intake_file).exists():
            db.execute(
                "INSERT INTO creator_intake (intake_id, creator_id, intake_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (new_id("in"), creator_id, Path(intake_file).read_text()[:50000], now(), now()))
    return creator_id


def select_deep_pass(creator_id, per_platform=DEEP_PASS_PER_PLATFORM):
    """Per platform: rank by engagement, recency and text richness."""
    with get_db() as db:
        db.execute("UPDATE content_items SET selected_for_deep_pass=0 WHERE creator_id=?", (creator_id,))
        platforms = [r["platform"] for r in db.execute(
            "SELECT DISTINCT platform FROM content_items WHERE creator_id=?", (creator_id,)).fetchall()]
        total = 0
        for platform in platforms:
            rows = [dict(r) for r in db.execute(
                "SELECT content_id, published_at, text_body, caption, description, metrics_json "
                "FROM content_items WHERE creator_id=? AND platform=?", (creator_id, platform)).fetchall()]
            if not rows:
                continue
            def metric(r):
                m = json.loads(r["metrics_json"] or "{}")
                return max(int(m.get(k, 0) or 0) for k in ("views", "likes", "like_count", "comments")) \
                    if m else 0
            max_m = max((metric(r) for r in rows), default=0) or 1
            recency = {r["content_id"]: i for i, r in enumerate(
                sorted(rows, key=lambda r: r["published_at"] or "", reverse=True))}
            scored = sorted(rows, key=lambda r: -(
                2.0 * metric(r) / max_m
                + 1.2 * (1 - recency[r["content_id"]] / max(len(rows) - 1, 1))
                + 0.8 * min(len(r["text_body"] or r["caption"] or r["description"] or "") / 800, 1)))
            picks = [r["content_id"] for r in scored[:per_platform]]
            db.executemany("UPDATE content_items SET selected_for_deep_pass=1 WHERE content_id=?",
                           [(c,) for c in picks])
            total += len(picks)
    return total


def fast_build(sources: dict, creator_id=None, intake_file=None, mode="preview",
               max_items=None, max_product_items=None, deep_pass=DEEP_PASS_PER_PLATFORM,
               include_non_product=False, background_enrich=True,
               skip_transcripts=False, skip_comments=False, force_refresh=False,
               run_id=None, on_progress=None):
    """sources: {platform: config_dict}. Returns summary dict.

    Modes: preview (small high-signal sample, fast), product (full catalog scan,
    process ALL product-related items), full (process everything).
    max_items=None means: preview defaults to 120; product/full fetch the FULL catalog.
    """
    t0 = time.time()
    run_id = run_id or new_id("run")
    creator_id = _ensure_creator(creator_id, sources, intake_file)
    if mode not in MODES:
        mode = "preview"
    fetch_limit = max_items if max_items else (120 if mode == "preview" else None)

    from creator_twin.build_locks import acquire, downgrade, heartbeat as lock_heartbeat, release
    lock_id, existing = acquire(creator_id, run_id)
    if not lock_id:
        update_run(run_id, status="error",
                   step_detail=f"A build is already running for this creator (run {existing})")
        raise RuntimeError(f"Build already running for this creator: {existing}")

    def check_cancel():
        with get_db() as db:
            r = db.execute("SELECT cancel_requested FROM build_runs WHERE run_id=?", (run_id,)).fetchone()
        if r and r["cancel_requested"]:
            raise BuildCancelled("Build stopped by user")

    def report(step, detail="", frac=None, **counts):
        check_cancel()
        lock_heartbeat(lock_id)
        fields = dict(step=step, step_detail=detail, **counts)
        if frac is not None:
            fields["progress"] = frac
        update_run(run_id, **fields)
        if on_progress:
            on_progress(step, detail, counts)
        log.info("[%s] %s", step, detail)

    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO build_runs (run_id, creator_id, status, step, started_at,"
            " platforms_requested_json, mode, requested_max_items, background_enrichment_enabled)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, creator_id, "running", "sources", now(), json.dumps(list(sources)),
             mode, max_items, 1 if background_enrich else 0))

    completed, failed = [], []
    saved_map = {}
    try:
        # Step 1+2: connectors — independent, failure-tolerant. product/full scan everything.
        n = max(len(sources), 1)
        for i, (platform, cfg) in enumerate(sources.items()):
            report("sources", f"Connecting {platform}...", 0.04 + 0.18 * i / n)
            cfg = dict(cfg, max_items=fetch_limit, deep_pass=deep_pass,
                       skip_transcripts=skip_transcripts, skip_comments=skip_comments,
                       force_refresh=force_refresh)
            try:
                stats = get_connector(platform)(
                    creator_id, cfg, run_id,
                    progress=lambda d: report("catalogscan", d)).run()
            except BuildCancelled:
                raise
            except Exception as e:
                stats = {"status": "failed", "errors": [str(e)]}
            (completed if stats["status"] == "completed" else failed).append(platform)
            saved_map[platform] = stats.get("items_saved", 0)
            label = ("found" if stats.get("items_saved") else
                     "needs auth/upload" if stats["status"] in ("completed", "skipped") else "unavailable")
            report("sources", f"{platform.capitalize()}: {label}")
            for err in stats.get("errors", [])[:3]:
                add_run_error(run_id, f"{platform}: {err}")
        update_run(run_id, platforms_completed_json=json.dumps(completed),
                   platforms_failed_json=json.dumps(failed))
        connected = [p for p, n in saved_map.items() if n > 0]
        if connected == ["youtube"] and len(sources) > 1:
            report("sources", "Using YouTube for now. Add Instagram or TikTok later.")
        elif len(connected) > 1:
            report("sources", "Combining " + ", ".join(p.capitalize() for p in connected) + ".")

        with get_db() as db:
            content_found = db.execute(
                "SELECT COUNT(*) AS n FROM content_items WHERE creator_id=?", (creator_id,)).fetchone()["n"]
            transcripts = db.execute(
                "SELECT COUNT(*) AS n FROM content_items WHERE creator_id=? AND source_type='real_transcript'",
                (creator_id,)).fetchone()["n"]
            n_comments = db.execute(
                "SELECT COUNT(*) AS n FROM comments WHERE content_id IN "
                "(SELECT content_id FROM content_items WHERE creator_id=?)", (creator_id,)).fetchone()["n"]
        report("catalogscan", f"Full catalog found: {content_found} items", 0.24,
               content_found=content_found, total_found=content_found, videos_found=content_found,
               transcripts_fetched=transcripts, comments_fetched=n_comments)
        if content_found == 0:
            raise RuntimeError("No content ingested from any source — check handles/exports/keys")

        # Step 3: product relevance classification (rule-based, free)
        from creator_twin.intelligence.product_relevance import classify_creator_content
        report("products", "Scoring every item for product relevance...", 0.26)
        pstats = classify_creator_content(creator_id, progress=lambda d: report("products", d))
        report("products", f"Product candidates found: {pstats['product_candidates']} of {pstats['total']}",
               0.30, product_candidates_found=pstats["product_candidates"])

        # Step 4: selection by mode
        picked = select_deep_pass(creator_id, deep_pass)
        with get_db() as db:
            db.execute("UPDATE content_items SET selected_for_fast_build=0 WHERE creator_id=?", (creator_id,))
            if mode == "full":
                db.execute("UPDATE content_items SET selected_for_fast_build=1 WHERE creator_id=?", (creator_id,))
            elif mode == "product":
                if max_product_items:
                    db.execute(
                        """UPDATE content_items SET selected_for_fast_build=1 WHERE content_id IN
                           (SELECT content_id FROM content_items WHERE creator_id=? AND is_product_related=1
                            ORDER BY product_relevance_score DESC LIMIT ?)""", (creator_id, max_product_items))
                else:
                    db.execute("UPDATE content_items SET selected_for_fast_build=1 WHERE creator_id=? AND is_product_related=1",
                               (creator_id,))
                if include_non_product:
                    db.execute("UPDATE content_items SET selected_for_fast_build=1 WHERE creator_id=? AND selected_for_deep_pass=1",
                               (creator_id,))
            else:  # preview: high-signal sample, product candidates first
                db.execute("""UPDATE content_items SET selected_for_fast_build=1 WHERE creator_id=?
                              AND (selected_for_deep_pass=1 OR is_product_related=1)""", (creator_id,))
            n_selected = db.execute(
                "SELECT COUNT(*) AS n FROM content_items WHERE creator_id=? AND selected_for_fast_build=1",
                (creator_id,)).fetchone()["n"]
        mode_label = {"preview": "Fast preview", "product": "Product catalog", "full": "Full catalog"}[mode]
        report("select", f"{mode_label}: {n_selected} items selected ({content_found} total found)", 0.33,
               selected_for_fast_build=n_selected)

        # ===== PHASE 1: first usable version =====
        report("fingerprint", "Building first taste model...", 0.40)
        generate_fingerprint(creator_id)
        report("fingerprint", "Taste model ready", 0.52)

        report("catalog", f"Learning from the top {min(FIRST_BATCH, n_selected)} product items...", 0.55)
        n_synth = generate_catalog(creator_id, force_refresh=force_refresh, scope="fast",
                                   batch_limit=FIRST_BATCH,
                                   progress=lambda d: report("catalog", d, 0.66),
                                   progress_label="First taste model")

        report("suggestions", "Creating starter suggestions...", 0.74)
        n_sugg = 0
        try:
            from creator_twin.intelligence.suggested_products import generate_suggested_products
            n_sugg = len(generate_suggested_products(creator_id))
        except Exception as e:
            add_run_error(run_id, f"suggestions: {e}")
        report("suggestions", f"{n_sugg} starter suggestions", 0.80, suggested_products_count=n_sugg)

        report("index", "Indexing first answers...", 0.84)
        n_chunks = build_chunks(creator_id, progress=lambda d: report("index", d, 0.92))
        index_pending(creator_id)

        with get_db() as db:
            remaining = db.execute(
                "SELECT COUNT(*) AS n FROM content_items WHERE creator_id=? AND processing_status='pending'",
                (creator_id,)).fetchone()["n"]

        # USABLE — route the user in; everything else is enrichment
        update_run(run_id, status="usable", is_usable=1, first_usable_at=now(), progress=1.0,
                   step="ready", step_detail="Ready. Still learning in the background.",
                   chunks_created=n_chunks, indexed_count=n_chunks, summarized_count=n_synth,
                   remaining_count=remaining, content_processed=n_synth, videos_processed=n_synth,
                   synthetic_items_generated=n_synth,
                   estimated_background_time_seconds=max(remaining * 3, 30) if remaining else 0)
        if on_progress:
            on_progress("ready", "First version ready", {})
        log.info("Build %s USABLE in %.0fs (%d summarized, %d chunks, %d remaining)",
                 run_id, time.time() - t0, n_synth, n_chunks, remaining)

        enrichment_thread = None
        if background_enrich:
            downgrade(lock_id)  # lock continues for enrichment phase
            import threading
            enrichment_thread = threading.Thread(
                target=_background_enrichment, daemon=True, name=f"enrich-{creator_id}",
                args=(creator_id, run_id, lock_id))
            enrichment_thread.start()
        else:
            release(lock_id)

        return {
            "enrichment_thread": enrichment_thread,
            "run_id": run_id, "creator_id": creator_id, "mode": mode,
            "platforms_processed": completed, "platforms_failed": failed,
            "total_found": content_found,
            "product_candidates_found": pstats["product_candidates"],
            "selected_for_fast_build": n_selected,
            "content_items_processed": n_synth,
            "remaining_for_enrichment": remaining,
            "real_transcripts_fetched": transcripts,
            "synthetic_notes_generated": n_synth,
            "comments_fetched": n_comments,
            "suggested_products": n_sugg,
            "chunks_created": n_chunks,
            "profile_json": str(PROFILE_DIR / f"{creator_id}_fingerprint.json"),
            "elapsed_seconds": round(time.time() - t0),
        }
    except BuildCancelled:
        release(lock_id)
        update_run(run_id, status="cancelled", step_detail="Stopped by user", finished_at=now())
        log.info("Build %s cancelled", run_id)
        raise
    except Exception as e:
        release(lock_id)
        add_run_error(run_id, str(e))
        update_run(run_id, status="error", step_detail=str(e)[:300], finished_at=now())
        raise


def _cancel_requested(run_id: str) -> bool:
    with get_db() as db:
        r = db.execute("SELECT cancel_requested FROM build_runs WHERE run_id=?", (run_id,)).fetchone()
    return bool(r and r["cancel_requested"])


def _background_enrichment(creator_id: str, run_id: str, lock_id: str = None):
    """Phase 2: everything beyond the first usable version. Never blocks the user."""
    from creator_twin.background_worker import enrich, pending_count
    from creator_twin.build_locks import release
    update_run(run_id, status="enriching", enrichment_status="running")

    def stage(label, fn):
        if _cancel_requested(run_id):
            raise BuildCancelled()
        update_run(run_id, enrichment_status=label)
        try:
            fn()
        except BuildCancelled:
            raise
        except Exception as e:
            add_run_error(run_id, f"enrich/{label}: {e}")

    try:
        stage("Enriching full catalog", lambda: enrich(creator_id))
        stage("Reading each platform", lambda: generate_platform_summaries(creator_id))
        stage("Modeling style", lambda: generate_style_model(creator_id))
        stage("Modeling audience", lambda: generate_audience_model(creator_id))
        stage("Commerce intelligence", lambda: generate_commerce_intelligence(creator_id))
        stage("Prepping buying questions", lambda: generate_qa_pairs(creator_id))
        stage("Improving recommendations", lambda: __import__(
            "creator_twin.intelligence.suggested_products", fromlist=["generate_suggested_products"]
        ).generate_suggested_products(creator_id))
        stage("Final indexing", lambda: build_chunks(creator_id))
        stage("Review packet", lambda: generate_review_packet(creator_id))
        with get_db() as db:
            n_chunks = db.execute("SELECT COUNT(*) AS n FROM rag_chunks WHERE creator_id=?",
                                  (creator_id,)).fetchone()["n"]
        update_run(run_id, status="completed", enrichment_status="done", finished_at=now(),
                   chunks_created=n_chunks, indexed_count=n_chunks,
                   remaining_count=pending_count(creator_id))
        log.info("Enrichment complete for %s", creator_id)
    except BuildCancelled:
        update_run(run_id, status="usable", enrichment_status="stopped", finished_at=now())
        log.info("Enrichment stopped by user for %s (twin stays usable)", creator_id)
    finally:
        if lock_id:
            release(lock_id)


def build_sources_from_args(args) -> dict:
    """Map CLI args to {platform: config}."""
    sources = {}
    yt_url = args.youtube_channel_url or getattr(args, "channel_url", None)
    yt_id = args.youtube_channel_id or getattr(args, "channel_id", None)
    if yt_url or yt_id:
        sources["youtube"] = {"channel_url": yt_url, "channel_id": yt_id}
    if args.instagram_handle or args.instagram_export:
        sources["instagram"] = {"handle": args.instagram_handle, "export_path": args.instagram_export}
    if args.tiktok_handle or args.tiktok_export:
        sources["tiktok"] = {"handle": args.tiktok_handle, "export_path": args.tiktok_export}
    if args.x_handle or args.x_archive:
        sources["x"] = {"handle": args.x_handle, "export_path": args.x_archive}
    if args.threads_handle or args.threads_export:
        sources["threads"] = {"handle": args.threads_handle, "export_path": args.threads_export}
    if args.website_url:
        sources["website"] = {"website_url": args.website_url, "max_pages": args.max_pages}
    if args.newsletter_dir:
        sources["newsletter"] = {"export_dir": args.newsletter_dir}
    if args.podcast_rss:
        sources["podcast"] = {"rss_url": args.podcast_rss}
    if args.uploaded_files_dir:
        sources["uploads"] = {"files_dir": args.uploaded_files_dir}
    return sources


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Creator Twin Fast Build (multi-platform)")
    ap.add_argument("--youtube-channel-url")
    ap.add_argument("--youtube-channel-id")
    ap.add_argument("--channel-url", help="(legacy alias for --youtube-channel-url)")
    ap.add_argument("--channel-id", help="(legacy alias for --youtube-channel-id)")
    ap.add_argument("--instagram-handle")
    ap.add_argument("--instagram-export", help="Instagram data export dir/file (JSON/CSV)")
    ap.add_argument("--tiktok-handle")
    ap.add_argument("--tiktok-export", help="TikTok export dir/file (JSON/CSV/URL list)")
    ap.add_argument("--x-handle")
    ap.add_argument("--x-archive", help="X archive dir or tweets.js/JSON/CSV")
    ap.add_argument("--threads-handle")
    ap.add_argument("--threads-export")
    ap.add_argument("--website-url")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--newsletter-dir")
    ap.add_argument("--podcast-rss")
    ap.add_argument("--uploaded-files-dir")
    ap.add_argument("--intake-file")
    ap.add_argument("--creator-id", help="add sources to an existing creator")
    ap.add_argument("--mode", choices=list(MODES), default="preview",
                    help="preview: fast sample | product: all product-related content | full: entire catalog")
    ap.add_argument("--max-items", type=int, default=None,
                    help="optional cap; omit for full catalog in product/full modes")
    ap.add_argument("--max-product-items", type=int, default=None)
    ap.add_argument("--max-items-per-platform", type=int, default=None, help="(legacy alias for --max-items)")
    ap.add_argument("--deep-pass", "--deep-pass-per-platform", dest="deep_pass",
                    type=int, default=DEEP_PASS_PER_PLATFORM)
    ap.add_argument("--background-enrich", default="true", choices=["true", "false"])
    ap.add_argument("--include-non-product", default="false", choices=["true", "false"])
    ap.add_argument("--skip-transcripts", action="store_true")
    ap.add_argument("--skip-comments", action="store_true")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--resume", action="store_true", help="reprocess only pending items")
    args = ap.parse_args()

    if args.resume and args.creator_id:
        from creator_twin.background_worker import enrich
        n = enrich(args.creator_id)
        print(f"Resumed: {n} pending items processed for {args.creator_id}")
        return

    sources = build_sources_from_args(args)
    if not sources:
        ap.error("Provide at least one source (e.g. --youtube-channel-url or --website-url)")

    s = fast_build(sources, creator_id=args.creator_id, intake_file=args.intake_file,
                   mode=args.mode, max_items=args.max_items or args.max_items_per_platform,
                   max_product_items=args.max_product_items, deep_pass=args.deep_pass,
                   include_non_product=args.include_non_product == "true",
                   background_enrich=args.background_enrich == "true",
                   skip_transcripts=args.skip_transcripts, skip_comments=args.skip_comments,
                   force_refresh=args.force_refresh)

    print("\n" + "=" * 64)
    print(f"  CREATOR TWIN — FIRST USABLE VERSION READY ({s['mode']} mode)")
    print("=" * 64)
    for k in ("creator_id", "platforms_processed", "platforms_failed", "total_found",
              "product_candidates_found", "selected_for_fast_build", "content_items_processed",
              "remaining_for_enrichment", "real_transcripts_fetched", "comments_fetched",
              "suggested_products", "chunks_created", "elapsed_seconds"):
        print(f"  {k:28s}: {s[k]}")
    print("=" * 64)
    t = s.get("enrichment_thread")
    if t:
        print("\nBackground enrichment running... (Ctrl+C keeps the usable twin)")
        t.join()
        print("Enrichment finished.")
    print(f"\nNext: python creator_twin/smoke_test.py --creator-id {s['creator_id']}\n")


if __name__ == "__main__":
    main()
