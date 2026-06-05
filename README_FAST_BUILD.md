# Creator Twin Fast Build

Turn any creator's **whole public presence** — not just YouTube — into a usable AI twin in under 60 minutes. The twin answers fan questions, recommends content, gives the creator's take on any product link, and drafts posts in their voice.

## What it does

1. **Connectors** ingest high-signal content per platform (profiles, posts, captions, metadata, top comments, transcripts where easy)
2. **Universal content model** normalizes everything into `content_items` with honest `source_type` + `confidence` labels
3. **Intelligence layer** builds: cross-platform creator fingerprint, per-platform summaries, per-item catalog notes, style model, audience model, commerce intelligence
4. **RAG index** (SQLite FTS5/BM25 — zero embedding keys; vector backend pluggable via `EMBEDDING_MODEL`)
5. **Creator review loop** — review packet → edit → approve → approved facts override inference

## Supported platforms

| Platform | Modes | Notes |
|---|---|---|
| YouTube | official API | channel, videos, playlists, comments, optional transcripts (30s cap, never blocks) |
| Instagram | `INSTAGRAM_ACCESS_TOKEN` (Graph API) or `--instagram-export` (data export JSON/CSV) | no scraping by default |
| TikTok | `--tiktok-export` (JSON/CSV/URL list); official API placeholder | |
| X/Twitter | `X_BEARER_TOKEN` or `--x-archive` (tweets.js/JSON/CSV) | threads preserved |
| Threads | `--threads-export` (JSON/CSV/TXT) | |
| Website/blog | `--website-url` | ≤25 pages, robots.txt respected, about/blog/product/faq prioritized |
| Newsletter | `--newsletter-dir` (HTML/MD/TXT/CSV, one issue per file) | |
| Podcast | `--podcast-rss` | metadata + feed-linked transcripts only; no audio download in fast mode |
| Uploaded files | `--uploaded-files-dir` (PDF/MD/TXT/CSV/HTML/VTT) | `creator_uploaded`, highest trust |

**No connector blocks the build.** If Instagram fails, YouTube still builds. Failures are recorded in `connector_runs` for later retry.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add keys
.venv/bin/python server/app.py   # web UI at http://127.0.0.1:7860
```

### YouTube-only
```bash
python creator_twin/fast_build.py --youtube-channel-url "https://youtube.com/@example"
```

### Multi-platform
```bash
python creator_twin/fast_build.py \
  --youtube-channel-url "https://youtube.com/@example" \
  --instagram-handle example --tiktok-handle example --x-handle example \
  --website-url "https://example.com" --podcast-rss "https://example.com/feed.xml" \
  --intake-file creator_intake.json \
  --max-items-per-platform 100 --deep-pass-per-platform 25
```

### Add sources to an existing creator
```bash
python creator_twin/fast_build.py --creator-id CR_ID --website-url "https://example.com"
```

### Uploads, tests, approval
```bash
python creator_twin/fast_build.py --creator-id CR_ID --uploaded-files-dir ./my_docs
python creator_twin/smoke_test.py --creator-id CR_ID
# review data/profiles/CR_ID_creator_review_packet.md, edit CR_ID_fingerprint.json, then:
python creator_twin/review/approve_profile.py --creator-id CR_ID
```

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `YOUTUBE_API_KEY` | for YouTube | Data API v3 |
| `ANTHROPIC_API_KEY` | yes | LLM (Claude) |
| `INSTAGRAM_ACCESS_TOKEN` | optional | Graph API mode |
| `X_BEARER_TOKEN` | optional | X API mode |
| `TIKTOK_ACCESS_TOKEN` | optional | future official adapter |
| `EMBEDDING_MODEL` | optional | default `fts5_bm25`; set to enable a vector backend |
| `ANTHROPIC_MODEL` | optional | model override |

## How facts are labeled

| source_type | trust |
|---|---|
| `creator_uploaded` / `creator_approved` | highest — overrides inference |
| `real_transcript` / `official_api` / `website_crawl` / `uploaded_export` | directly sourced |
| `metadata_inferred_notes` / `ai_inferred_*` | AI-inferred, hedged in answers |

Synthetic notes are **never** presented as transcripts. Answers expose platform, source_type and confidence per supporting chunk. The assistant never claims to be the creator unless `first_person_draft` mode is explicitly chosen.

## Architecture

```
creator_twin/
  fast_build.py            # orchestrator (multi-platform)
  connectors/              # base.py + 9 platform connectors
  normalization/           # universal content model writers
  intelligence/            # fingerprint, catalog, style, audience, pillars, commerce
  rag/                     # chunker, embedder, retriever, citations
  review/                  # review_packet, approve_profile
  db.py, prompts.py, chat.py, smoke_test.py
```

## Limitations

- Instagram/TikTok/Threads without tokens or exports store a profile stub only (no scraping by design)
- Podcast audio is not transcribed in fast mode (background enrichment can add it)
- FTS5 keyword retrieval until a vector backend is wired
- Revenue/conversion numbers are never inferred — only creator-uploaded data

## Adding a connector

Subclass `connectors/base.py:BaseConnector`, implement `validate_config / fetch_profile / fetch_content / normalize_*`, register it in `connectors/__init__.py`. The base class handles persistence, connector_runs tracking, and failure isolation.
