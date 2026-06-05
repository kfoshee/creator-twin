# Creator Twin — Deployment Guide

## 1. Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add YOUTUBE_API_KEY + ANTHROPIC_API_KEY
.venv/bin/python server/app.py        # serves frontend + API at http://127.0.0.1:7860
```

Run a build from the CLI:

```bash
# fast preview (default, ~120-item high-signal sample)
python creator_twin/fast_build.py --youtube-channel-url "https://youtube.com/@example"

# product mode: scan the FULL catalog, process every product-related item
python creator_twin/fast_build.py --youtube-channel-url "..." --mode product

# full mode: process the entire catalog
python creator_twin/fast_build.py --youtube-channel-url "..." --mode full

# resume / background-enrich pending items
python creator_twin/background_worker.py --creator-id CR_ID
```

## 2. GitHub Pages static demo

**Reality check:** GitHub Pages only hosts static files. It cannot run the
Python backend or hold API keys. The Pages build is a frontend demo using
prebuilt JSON data — no live ingestion, no chat against the real LLM.

Setup:
1. Export demo data from your local build: `python scripts/export_demo_data.py`
2. Commit and push to `main`.
3. In repo Settings → Pages → Source: **GitHub Actions**.
4. The included workflow `.github/workflows/deploy-pages.yml` builds `dist/`
   (`scripts/build_static_demo.sh` — sets `DEMO_MODE: true` in `config.js`)
   and deploys it.

Limitations: demo data only, canned chat replies, builds disabled.

## 3. Real production deployment

```
Frontend  → Vercel (or GitHub Pages, with API_BASE_URL pointing at backend)
Backend   → Render / Railway / Fly.io / Vercel Fluid Compute (Python FastAPI)
Database  → Supabase Postgres (swap SQLite via DATABASE_URL) or keep SQLite on a persistent disk
Storage   → Supabase Storage / S3 (uploads, exports)
Jobs      → background_worker.py as a worker process, or a queue (Vercel Queues, etc.)
```

Backend env vars:

| Var | Purpose |
|---|---|
| `YOUTUBE_API_KEY` | YouTube ingest |
| `ANTHROPIC_API_KEY` | LLM (required) |
| `DATABASE_URL` | optional Postgres |
| `INSTAGRAM_ACCESS_TOKEN`, `X_BEARER_TOKEN`, `TIKTOK_ACCESS_TOKEN` | optional connectors |
| `EMBEDDING_MODEL` | optional vector backend |

Frontend config (`web/config.js`): set `API_BASE_URL` to the backend URL,
`DEMO_MODE: false`.

> **Security:** never put private API keys in frontend config or any
> `*_PUBLIC_*` env var. All keys live on the backend only.

### API surface (frontend is written against these)

```
POST /api/build/start                       start a build {source, mode}
GET  /api/build/:run_id/status              progress, counts, ETA inputs
POST /api/build/:run_id/stop                cancel a running build
GET  /api/creators                          list twins
GET  /api/creators/:id                      profile + stats
GET  /api/creators/:id/content              catalog with product scores
GET  /api/creators/:id/progress             enrichment progress
POST /api/creators/:id/ask                  chat (persona_mode supported)
POST /api/creators/:id/enrich/start         kick background enrichment
```

## 4. Build modes

| Mode | What it does | When |
|---|---|---|
| `preview` | high-signal sample (~120 items + all product candidates in sample), fastest | first demo |
| `product` | scans the **full** catalog metadata, processes **all** product-related items | the real product |
| `full` | processes the entire catalog | completionist |
| background enrichment | after any build, remaining items keep processing; twin is usable immediately | automatic (`--background-enrich true`) |

Counts shown in the UI distinguish **total found** (true catalog size) from
**selected/summarized** (what the fast pass covered) — a "120" is never
presented as the whole catalog unless that's all the channel has.
