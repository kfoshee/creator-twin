# Deploy the REAL Creator Twin (not the demo)

Architecture: **GitHub Pages** serves the static frontend → it calls the **Python backend on Render** (your API keys live only there) → data persists on a Render disk (SQLite; Supabase Postgres is the future upgrade path).

## Step 1 — Deploy the backend on Render (~5 minutes)

1. Go to https://dashboard.render.com/blueprints → **New Blueprint Instance**
2. Connect the GitHub repo `kfoshee/creator-twin` — Render reads `render.yaml` automatically
3. When prompted for env vars, set the two secrets:
   - `ANTHROPIC_API_KEY` = your Anthropic key
   - `YOUTUBE_API_KEY` = your YouTube Data API key
   (everything else — `DATA_DIR=/data`, `APP_ENV=production`, `CORS_ORIGINS=https://kfoshee.github.io` — is preconfigured)
4. Click **Apply**. First deploy takes ~2 minutes.

> Plan note: `render.yaml` uses the **starter** plan ($7/mo) because it includes a persistent
> disk — your creators/builds survive deploys. The free plan works but wipes data on every
> deploy and sleeps after idle.

## Step 2 — Verify the backend

Open `https://creator-twin-api.onrender.com/api/health` (your URL may differ — copy it from the Render dashboard).

Expect:
```json
{"status":"ok","mode":"production","demo_mode":false,"database":"sqlite",
 "youtube_key_present":true,"anthropic_key_present":true,"llm_provider":"anthropic"}
```

## Step 3 — Point the frontend at the backend

```bash
gh variable set API_BASE_URL --repo kfoshee/creator-twin --body "https://creator-twin-api.onrender.com"
gh workflow run deploy-pages.yml --repo kfoshee/creator-twin
```
(or GitHub → repo Settings → Secrets and variables → Actions → **Variables** → new variable `API_BASE_URL`)

The Pages build writes `config.js` with `DEMO_MODE: false` and your backend URL — at runtime the frontend loads this config and talks to the live API.

## Step 4 — Confirm it's live

Open https://kfoshee.github.io/creator-twin/ — the bottom-right badge must say **"Live API · GitHub Pages"**, not "Demo mode". Add a real YouTube creator and watch a real build run.

If the badge says **"Backend unavailable"**: the backend URL is wrong, the service is asleep (free plan), or CORS_ORIGINS doesn't include `https://kfoshee.github.io`.

## Environment variables reference

Backend (Render):
| Var | Value |
|---|---|
| `ANTHROPIC_API_KEY` | secret — the only LLM provider |
| `YOUTUBE_API_KEY` | secret |
| `DATA_DIR` | `/data` (persistent disk mount) |
| `APP_ENV` | `production` |
| `DEMO_MODE` | `false` |
| `CORS_ORIGINS` | `https://kfoshee.github.io` |
| `FRONTEND_URL` | `https://kfoshee.github.io/creator-twin` |

Frontend (GitHub repo *variable*, not secret):
| Var | Value |
|---|---|
| `API_BASE_URL` | your Render service URL |

**No private keys ever go in the frontend or the repo.** `.env` is gitignored.

## Demo mode (optional, not the default)

Without `API_BASE_URL` set, the Pages build falls back to the keyless demo (canned data). Set the variable and re-run the workflow to switch to live. Local dev is unchanged: `.venv/bin/python server/app.py`.

## Database upgrade path

Production currently uses SQLite on the Render disk (single-writer queue, WAL — solid for one instance). For multi-instance scale, the upgrade is Supabase Postgres via `DATABASE_URL` + porting the FTS5 index to Postgres full-text search; the schema/migrations are centralized in `creator_twin/db.py` to make that a contained change.
