"""Creator Twin web server: build API + live progress + chat."""
import json
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from creator_twin.chat import ask
from creator_twin.config import PROFILE_DIR
from creator_twin.creator_fingerprint import get_fingerprint
from creator_twin.db import get_db, new_id, now
from creator_twin.fast_build import STEPS, fast_build
from creator_twin.llm import provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("creator_twin.server")

app = FastAPI(title="Creator Twin Fast Build")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# startup hygiene: stale runs/locks from crashed sessions must not block new builds
try:
    from creator_twin.build_locks import cleanup_stale
    cleanup_stale()
except Exception:
    log.exception("startup cleanup failed")

# in-memory live log per run (DB holds durable state)
RUN_LOGS: dict = {}


# Standard build — identical for everyone.
STANDARD_MAX_ITEMS = 120
STANDARD_DEEP_PASS = 30


class BuildRequest(BaseModel):
    source: str = ""          # universal: any creator link/handle, platform auto-detected
    platform: str = "auto"    # auto | all | youtube | instagram | tiktok | x | website | podcast
    mode: str = "product"     # one standard mode: full catalog scan, all product videos
    channel_url: str = ""
    instagram_handle: str = ""
    tiktok_handle: str = ""
    x_handle: str = ""
    website_url: str = ""
    podcast_rss: str = ""


def _path_handle(url: str) -> str:
    import urllib.parse
    path = urllib.parse.urlparse(url if "://" in url else "https://" + url).path
    seg = next((s for s in path.split("/") if s), "")
    return seg.lstrip("@")


def detect_source(s: str):
    """Map any pasted link/handle to (platform, connector_config)."""
    t = s.strip()
    low = t.lower()
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube", {"channel_url": t}
    if "instagram.com" in low:
        return "instagram", {"handle": _path_handle(t)}
    if "tiktok.com" in low:
        return "tiktok", {"handle": _path_handle(t)}
    if "twitter.com" in low or low.startswith("x.com") or "//x.com" in low:
        return "x", {"handle": _path_handle(t)}
    if "threads.net" in low:
        return "threads", {"handle": _path_handle(t)}
    if low.endswith((".xml", ".rss")) or "rss" in low or "/feed" in low:
        return "podcast", {"rss_url": t}
    if t.startswith("@"):
        return "youtube", {"channel_url": t}
    if "." in t:
        return "website", {"website_url": t}
    return "youtube", {"channel_url": t}


class ChatRequest(BaseModel):
    creator_id: str
    message: str
    persona_mode: str = "first_person_creator_take"
    history: list = []
    session_id: str = ""


NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers=NO_CACHE)


@app.get("/config.js")
def config_js():
    return FileResponse(WEB_DIR / "config.js", headers=NO_CACHE)


@app.get("/hosting.js")
def hosting_js():
    return FileResponse(WEB_DIR / "hosting.js", headers=NO_CACHE)


@app.get("/api/health")
def health():
    import creator_twin.config as cfg
    return {"ok": True, "youtube_key": bool(cfg.YOUTUBE_API_KEY), "llm_provider": provider()}


@app.post("/api/build")
def start_build(req: BuildRequest):
    if provider() == "none":
        raise HTTPException(400, "No LLM key set — add ANTHROPIC_API_KEY or GEMINI_API_KEY to .env")

    sources = {}
    if req.source.strip():
        from creator_twin.source_detection import detect_creator_source
        det = detect_creator_source(req.source)
        sel = req.platform
        if sel == "auto":
            sel = det["primary_platform"] or "all"   # plain handles NEVER default to YouTube
        if sel == "all":
            handle = det["normalized_handle"] or req.source.strip().lstrip("@")
            if det["primary_platform"] == "youtube":
                sources["youtube"] = {"channel_url": req.source.strip()}
            else:
                sources["youtube"] = {"channel_url": "@" + handle}
            for p in ("instagram", "tiktok", "x"):
                if det["primary_platform"] != p:
                    sources[p] = {"handle": handle}
            if det["primary_platform"] in ("instagram", "tiktok", "x"):
                sources[det["primary_platform"]] = {"handle": det["normalized_handle"]}
        elif sel == "youtube":
            sources["youtube"] = {"channel_url": req.source.strip()}
        elif sel in ("instagram", "tiktok", "x", "threads"):
            sources[sel] = {"handle": det["normalized_handle"] or req.source.strip().lstrip("@")}
        elif sel == "website":
            sources["website"] = {"website_url": req.source.strip()}
        elif sel == "podcast":
            sources["podcast"] = {"rss_url": req.source.strip()}
        else:
            platform, cfg = detect_source(req.source)
            sources[platform] = cfg
    if req.channel_url.strip():
        sources["youtube"] = {"channel_url": req.channel_url.strip()}
    if req.instagram_handle.strip():
        sources["instagram"] = {"handle": req.instagram_handle.strip()}
    if req.tiktok_handle.strip():
        sources["tiktok"] = {"handle": req.tiktok_handle.strip()}
    if req.x_handle.strip():
        sources["x"] = {"handle": req.x_handle.strip()}
    if req.website_url.strip():
        sources["website"] = {"website_url": req.website_url.strip()}
    if req.podcast_rss.strip():
        sources["podcast"] = {"rss_url": req.podcast_rss.strip()}
    if not sources:
        raise HTTPException(400, "Add at least one source")
    import creator_twin.config as cfg
    if "youtube" in sources and not cfg.YOUTUBE_API_KEY:
        raise HTTPException(400, "YOUTUBE_API_KEY is not set in .env")

    run_id = new_id("run")
    RUN_LOGS[run_id] = []

    def on_progress(step, detail, counts):
        RUN_LOGS[run_id].append({"time": now(), "step": step, "detail": detail})
        RUN_LOGS[run_id] = RUN_LOGS[run_id][-200:]

    mode = req.mode if req.mode in ("preview", "product", "full") else "preview"

    def worker():
        try:
            summary = fast_build(sources, mode=mode, deep_pass=STANDARD_DEEP_PASS,
                                 run_id=run_id, on_progress=on_progress)
            RUN_LOGS[run_id].append({"time": now(), "step": "done",
                                     "detail": f"Build complete in {summary['elapsed_seconds']}s"})
        except Exception as e:
            log.exception("Build ended")
            RUN_LOGS[run_id].append({"time": now(), "step": "error", "detail": str(e)[:300]})

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id, "mode": mode, "steps": [{"key": k, "label": l} for k, l in STEPS]}


@app.post("/api/build/{run_id}/stop")
def stop_build(run_id: str):
    with get_db() as db:
        r = db.execute("SELECT status, is_usable, creator_id FROM build_runs WHERE run_id=?",
                       (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown run_id")
    from creator_twin.db_writer import flush, write
    write(lambda c: c.execute("UPDATE build_runs SET cancel_requested=1 WHERE run_id=?", (run_id,)), wait=True)
    write(lambda c: c.execute(
        "UPDATE build_locks SET status='released' WHERE run_id=? AND status='active'", (run_id,)))
    flush()
    # if already usable, only enrichment stops — the twin stays
    return {"ok": True, "stopping": True, "keeps_usable_twin": bool(r["is_usable"])}


@app.get("/api/build/{run_id}")
def build_status(run_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM build_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown run_id")
    d = dict(row)
    d["errors"] = json.loads(d.pop("errors_json") or "[]")
    d["log"] = RUN_LOGS.get(run_id, [])[-30:]
    d["is_usable"] = bool(d.get("is_usable"))
    d["phase"] = ("done" if d["status"] in ("completed", "done")
                  else "background_enrichment" if d["status"] in ("usable", "enriching") and d["is_usable"]
                  else "first_build")
    d["progress_percent"] = round((d.get("progress") or 0) * 100)
    d["current_step"] = d.get("step_detail") or d.get("step")
    return d


@app.get("/api/debug/db")
def debug_db():
    from creator_twin.config import DB_PATH
    from creator_twin.db_writer import queue_length
    import os
    with get_db() as db:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        busy = db.execute("PRAGMA busy_timeout").fetchone()[0]
        runs = [dict(r) for r in db.execute(
            "SELECT run_id, creator_id, status, step, started_at FROM build_runs "
            "WHERE status IN ('running','usable','enriching') ORDER BY started_at DESC LIMIT 10").fetchall()]
        locks = [dict(r) for r in db.execute(
            "SELECT * FROM build_locks WHERE status='active'").fetchall()]
    import threading
    workers = [t.name for t in threading.enumerate() if t.name.startswith(("enrich-", "db-writer"))]
    return {"db_path": str(DB_PATH), "journal_mode": mode, "busy_timeout": busy,
            "active_build_runs": runs, "active_locks": locks,
            "writer_queue_length": queue_length(), "background_workers": workers,
            "current_process_id": os.getpid()}


@app.get("/api/creators")
def creators():
    with get_db() as db:
        rows = db.execute(
            """SELECT c.*, COALESCE(NULLIF(c.channel_title,''), c.display_name, c.primary_handle, c.creator_id) AS channel_title,
               (SELECT COUNT(*) FROM content_items ci WHERE ci.creator_id=c.creator_id) AS videos_in_db,
               (SELECT COUNT(*) FROM rag_chunks r WHERE r.creator_id=c.creator_id) AS chunks,
               (SELECT status FROM build_runs r WHERE r.creator_id=c.creator_id
                ORDER BY started_at DESC LIMIT 1) AS build_status
               FROM creators c WHERE c.deleted_at IS NULL ORDER BY c.updated_at DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["channel_title"] = (r["channel_title"] or d.get("display_name")
                              or d.get("primary_handle") or d["creator_id"])
        out.append(d)
    return out


class DiscoverRequest(BaseModel):
    input: str
    selected_platforms: object = "all"


@app.post("/api/creators/discover")
def discover_creator(req: DiscoverRequest):
    from creator_twin.source_detection import ALL_PLATFORMS, detect_creator_source, profile_url_for
    det = detect_creator_source(req.input)
    handle = det["normalized_handle"]
    platforms = (ALL_PLATFORMS if req.selected_platforms == "all"
                 else [p for p in (req.selected_platforms or []) if p in ALL_PLATFORMS])
    candidates = []
    for p in platforms:
        cand = {"platform": p, "handle": handle, "display_name": handle,
                "profile_url": profile_url_for(p, handle), "avatar_url": "",
                "follower_count": None, "confidence": 0.5, "source": "normalized_url",
                "available": "needs_verification"}
        if p == "youtube":
            try:
                from creator_twin.youtube_metadata import api_get
                data = api_get("search", {"part": "snippet", "type": "channel",
                                          "q": handle, "maxResults": 1})
                items = data.get("items", [])
                if items:
                    sn = items[0]["snippet"]
                    cand.update(display_name=sn.get("title", handle),
                                profile_url=f"https://www.youtube.com/channel/{sn['channelId']}",
                                avatar_url=(sn.get("thumbnails", {}).get("default") or {}).get("url", ""),
                                confidence=0.85, source="youtube_api", available="found")
            except Exception:
                pass
        candidates.append(cand)
    return {"normalized_handle": handle, "candidates": candidates,
            "recommended_sources": [c["platform"] for c in candidates if c["available"] == "found"] or ["youtube"]}


@app.delete("/api/creators/{creator_id}")
def delete_creator(creator_id: str):
    from creator_twin.db_writer import write
    with get_db() as db:
        c = db.execute("SELECT creator_id FROM creators WHERE creator_id=? AND deleted_at IS NULL",
                       (creator_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Unknown creator")

    def apply(conn):
        # stop any active build/enrichment, release locks, soft-delete
        conn.execute("UPDATE build_runs SET cancel_requested=1 WHERE creator_id=? AND status IN ('running','usable','enriching')",
                     (creator_id,))
        conn.execute("UPDATE build_locks SET status='released' WHERE creator_id=? AND status='active'",
                     (creator_id,))
        conn.execute("UPDATE creators SET deleted_at=?, deleted_reason='user_removed' WHERE creator_id=?",
                     (now(), creator_id))
    write(apply, wait=True)
    return {"ok": True, "removed": creator_id}


@app.get("/api/creators/{creator_id}")
def creator_detail(creator_id: str):
    with get_db() as db:
        c = db.execute("SELECT * FROM creators WHERE creator_id=?", (creator_id,)).fetchone()
        if not c:
            raise HTTPException(404, "Unknown creator")
        videos = [dict(r) for r in db.execute(
            """SELECT v.video_id, v.title, v.view_count, v.thumbnail_url, v.video_url,
                      v.selected_for_deep_pass, v.transcript_status,
                      (SELECT vc.source_type FROM video_content vc WHERE vc.video_id=v.video_id
                       AND vc.content_type='catalog_notes' ORDER BY vc.id DESC LIMIT 1) AS notes_source
               FROM videos v WHERE v.creator_id=? ORDER BY v.view_count DESC LIMIT 50""",
            (creator_id,)).fetchall()]
        stats = dict(db.execute(
            """SELECT
               (SELECT COUNT(*) FROM videos WHERE creator_id=?) AS videos,
               (SELECT COUNT(*) FROM videos WHERE creator_id=? AND transcript_status='fetched') AS transcripts,
               (SELECT COUNT(*) FROM rag_chunks WHERE creator_id=?) AS chunks,
               (SELECT COUNT(*) FROM qa_pairs WHERE creator_id=?) AS qa_pairs""",
            (creator_id,) * 4).fetchone())
    with get_db() as db:
        sources = [dict(r) for r in db.execute(
            """SELECT platform, handle, profile_url, follower_count, source_status
               FROM creator_profiles WHERE creator_id=?""", (creator_id,)).fetchall()]
    profile = get_fingerprint(creator_id)
    packet = PROFILE_DIR / f"{creator_id}_creator_review_packet.md"
    return {"creator": dict(c), "videos": videos, "stats": stats, "profile": profile,
            "sources": sources,
            "review_packet": packet.read_text() if packet.exists() else None}


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        return ask(req.creator_id, req.message, persona_mode=req.persona_mode,
                   history=req.history, session_id=req.session_id or None)
    except Exception as e:
        log.exception("Chat failed")
        return JSONResponse({"error": str(e)[:300]}, status_code=500)


class TakeRequest(BaseModel):
    product_input: str
    product_metadata: dict = {}
    session_id: str = ""
    persona_mode: str = "first_person_creator_take"


@app.post("/api/creators/{creator_id}/take")
def creator_take(creator_id: str, req: TakeRequest):
    return ask(creator_id, req.product_input, persona_mode=req.persona_mode,
               session_id=req.session_id or None)


class SessionChatRequest(BaseModel):
    session_id: str = ""
    message: str
    persona_mode: str = "first_person_creator_take"


@app.post("/api/creators/{creator_id}/chat")
def creator_session_chat(creator_id: str, req: SessionChatRequest):
    return ask(creator_id, req.message, persona_mode=req.persona_mode,
               session_id=req.session_id or None)


# ---- clean backend API aliases (Part G) ----

@app.post("/api/build/start")
def build_start_alias(req: BuildRequest):
    return start_build(req)


@app.get("/api/build/{run_id}/status")
def build_status_alias(run_id: str):
    return build_status(run_id)


@app.get("/api/creators/{creator_id}/content")
def creator_content(creator_id: str, limit: int = 100):
    with get_db() as db:
        rows = db.execute(
            """SELECT content_id, platform, content_type, title, caption, canonical_url,
                      published_at, is_product_related, product_relevance_score, processing_status
               FROM content_items WHERE creator_id=?
               ORDER BY product_relevance_score DESC LIMIT ?""", (creator_id, limit)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/creators/{creator_id}/progress")
def creator_progress(creator_id: str):
    with get_db() as db:
        stats = dict(db.execute(
            """SELECT COUNT(*) AS total_found,
               SUM(is_product_related) AS product_candidates,
               SUM(CASE WHEN processing_status='summarized' THEN 1 ELSE 0 END) AS summarized,
               SUM(CASE WHEN processing_status='pending' THEN 1 ELSE 0 END) AS remaining,
               SUM(CASE WHEN processing_status='failed' THEN 1 ELSE 0 END) AS failed
               FROM content_items WHERE creator_id=?""", (creator_id,)).fetchone())
        stats["chunks"] = db.execute(
            "SELECT COUNT(*) AS n FROM rag_chunks WHERE creator_id=?", (creator_id,)).fetchone()["n"]
    return stats


@app.post("/api/creators/{creator_id}/ask")
def creator_ask(creator_id: str, req: ChatRequest):
    req.creator_id = creator_id
    return chat(req)


class ResolveRequest(BaseModel):
    input: str


@app.get("/api/products/search")
def products_search(q: str = ""):
    from creator_twin.product_search import search_products
    try:
        return {"results": search_products(q)}
    except Exception as e:
        log.exception("product search failed")
        return {"results": [], "error": str(e)[:200]}


@app.post("/api/products/resolve")
def products_resolve(req: ResolveRequest):
    from creator_twin.product_search import resolve_input
    try:
        return resolve_input(req.input)
    except Exception as e:
        log.exception("product resolve failed")
        return {"type": "unknown", "results": [], "error": str(e)[:200]}


@app.get("/api/creators/{creator_id}/suggested-products")
def suggested_products(creator_id: str, limit: int = 8):
    from creator_twin.intelligence.suggested_products import get_suggested_products
    try:
        return get_suggested_products(creator_id, limit)
    except Exception as e:
        log.exception("suggestions failed")
        return JSONResponse({"error": str(e)[:200], "suggestions": []}, status_code=200)


@app.post("/api/creators/{creator_id}/suggested-products/regenerate")
def regenerate_suggestions(creator_id: str, limit: int = 8):
    from creator_twin.intelligence.suggested_products import get_suggested_products
    return get_suggested_products(creator_id, limit, regenerate=True)


@app.post("/api/creators/{creator_id}/enrich/start")
def enrich_start(creator_id: str):
    from creator_twin.background_worker import pending_count, start_background_enrichment
    n = pending_count(creator_id)
    if n:
        start_background_enrichment(creator_id)
    return {"ok": True, "pending": n, "started": bool(n)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7860)
