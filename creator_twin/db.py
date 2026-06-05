"""SQLite database layer for Creator Twin."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    creator_id TEXT PRIMARY KEY,
    channel_id TEXT UNIQUE,
    channel_url TEXT,
    channel_title TEXT,
    channel_description TEXT,
    custom_url TEXT,
    subscriber_count INTEGER,
    video_count INTEGER,
    view_count INTEGER,
    thumbnail_url TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    creator_id TEXT,
    title TEXT,
    description TEXT,
    published_at TEXT,
    duration_seconds INTEGER,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    playlist_ids TEXT,
    thumbnail_url TEXT,
    video_url TEXT,
    metadata_json TEXT,
    selected_for_deep_pass INTEGER DEFAULT 0,
    transcript_status TEXT DEFAULT 'pending',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS video_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    content_type TEXT,
    source_type TEXT,
    confidence TEXT,
    text TEXT,
    json_payload TEXT,
    synthetic_generated INTEGER DEFAULT 0,
    approved_by_creator INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_content_video ON video_content(video_id, content_type);

CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    video_id TEXT,
    author_name TEXT,
    text TEXT,
    like_count INTEGER,
    published_at TEXT,
    is_top_comment INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);

CREATE TABLE IF NOT EXISTS creator_fingerprint (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id TEXT,
    source_type TEXT,
    confidence TEXT,
    profile_json TEXT,
    approved_by_creator INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id TEXT PRIMARY KEY,
    creator_id TEXT,
    video_id TEXT,
    chunk_type TEXT,
    source_type TEXT,
    confidence TEXT,
    text TEXT,
    metadata_json TEXT,
    embedding_status TEXT DEFAULT 'pending',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_creator ON rag_chunks(creator_id);

CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
    text, chunk_id UNINDEXED, creator_id UNINDEXED
);

CREATE TABLE IF NOT EXISTS qa_pairs (
    qa_id TEXT PRIMARY KEY,
    creator_id TEXT,
    video_id TEXT,
    question TEXT,
    answer TEXT,
    source_type TEXT,
    confidence TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS creator_profiles (
    profile_id TEXT PRIMARY KEY,
    creator_id TEXT,
    platform TEXT,
    platform_user_id TEXT,
    handle TEXT,
    profile_url TEXT,
    bio TEXT,
    follower_count INTEGER,
    following_count INTEGER,
    post_count INTEGER,
    avatar_url TEXT,
    verified_status TEXT,
    raw_json TEXT,
    fetched_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_profiles_creator ON creator_profiles(creator_id, platform);

CREATE TABLE IF NOT EXISTS content_items (
    content_id TEXT PRIMARY KEY,
    creator_id TEXT,
    platform TEXT,
    platform_content_id TEXT,
    canonical_url TEXT,
    content_type TEXT,
    title TEXT,
    caption TEXT,
    description TEXT,
    text_body TEXT,
    published_at TEXT,
    duration_seconds INTEGER,
    thumbnail_url TEXT,
    media_urls_json TEXT,
    hashtags_json TEXT,
    mentions_json TEXT,
    metrics_json TEXT,
    raw_json TEXT,
    source_type TEXT,
    confidence TEXT,
    selected_for_deep_pass INTEGER DEFAULT 0,
    fetched_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_content_creator ON content_items(creator_id, platform);

CREATE TABLE IF NOT EXISTS content_media (
    media_id TEXT PRIMARY KEY,
    content_id TEXT,
    media_type TEXT,
    media_url TEXT,
    local_path TEXT,
    thumbnail_url TEXT,
    width INTEGER,
    height INTEGER,
    duration_seconds INTEGER,
    alt_text TEXT,
    ocr_text TEXT,
    transcript_text TEXT,
    media_analysis_json TEXT,
    source_type TEXT,
    confidence TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS creator_intake (
    intake_id TEXT PRIMARY KEY,
    creator_id TEXT,
    intake_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS platform_summaries (
    summary_id TEXT PRIMARY KEY,
    creator_id TEXT,
    platform TEXT,
    summary_json TEXT,
    confidence TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS content_summaries (
    summary_id TEXT PRIMARY KEY,
    content_id TEXT,
    source_type TEXT,
    synthetic_generated INTEGER DEFAULT 0,
    confidence TEXT,
    summary_json TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_csumm_content ON content_summaries(content_id);

CREATE TABLE IF NOT EXISTS connector_runs (
    connector_run_id TEXT PRIMARY KEY,
    run_id TEXT,
    creator_id TEXT,
    platform TEXT,
    status TEXT,
    started_at TEXT,
    finished_at TEXT,
    items_found INTEGER DEFAULT 0,
    items_saved INTEGER DEFAULT 0,
    items_skipped INTEGER DEFAULT 0,
    errors_json TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS build_runs (
    run_id TEXT PRIMARY KEY,
    creator_id TEXT,
    status TEXT,
    step TEXT,
    step_detail TEXT,
    progress REAL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    videos_found INTEGER DEFAULT 0,
    videos_processed INTEGER DEFAULT 0,
    transcripts_fetched INTEGER DEFAULT 0,
    transcripts_skipped INTEGER DEFAULT 0,
    comments_fetched INTEGER DEFAULT 0,
    synthetic_items_generated INTEGER DEFAULT 0,
    chunks_created INTEGER DEFAULT 0,
    errors_json TEXT DEFAULT '[]'
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=30000",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA temp_store=MEMORY",
)
_pragmas_logged = False


@contextmanager
def get_db():
    global _pragmas_logged
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    for p in PRAGMAS:
        conn.execute(p)
    if not _pragmas_logged:
        _pragmas_logged = True
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        import logging
        logging.getLogger("creator_twin.db").info(
            "SQLite ready: journal_mode=%s busy_timeout=30000 synchronous=NORMAL path=%s", mode, DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# columns added after the first release — applied idempotently to existing DBs
MIGRATIONS = [
    "ALTER TABLE creators ADD COLUMN display_name TEXT",
    "ALTER TABLE creators ADD COLUMN primary_platform TEXT DEFAULT 'youtube'",
    "ALTER TABLE creators ADD COLUMN primary_handle TEXT",
    "ALTER TABLE creators ADD COLUMN website_url TEXT",
    "ALTER TABLE creators ADD COLUMN short_bio TEXT",
    "ALTER TABLE comments ADD COLUMN content_id TEXT",
    "ALTER TABLE comments ADD COLUMN platform TEXT DEFAULT 'youtube'",
    "ALTER TABLE comments ADD COLUMN author_handle TEXT",
    "ALTER TABLE comments ADD COLUMN reply_count INTEGER DEFAULT 0",
    "ALTER TABLE comments ADD COLUMN raw_json TEXT",
    "ALTER TABLE rag_chunks ADD COLUMN platform TEXT",
    "ALTER TABLE qa_pairs ADD COLUMN platform TEXT",
    "ALTER TABLE build_runs ADD COLUMN platforms_requested_json TEXT DEFAULT '[]'",
    "ALTER TABLE build_runs ADD COLUMN platforms_completed_json TEXT DEFAULT '[]'",
    "ALTER TABLE build_runs ADD COLUMN platforms_failed_json TEXT DEFAULT '[]'",
    "ALTER TABLE build_runs ADD COLUMN content_found INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN content_processed INTEGER DEFAULT 0",
    # build modes + product discovery
    "ALTER TABLE content_items ADD COLUMN is_product_related INTEGER DEFAULT 0",
    "ALTER TABLE content_items ADD COLUMN product_relevance_score REAL DEFAULT 0",
    "ALTER TABLE content_items ADD COLUMN product_relevance_reasons_json TEXT DEFAULT '[]'",
    "ALTER TABLE content_items ADD COLUMN likely_products_json TEXT DEFAULT '[]'",
    "ALTER TABLE content_items ADD COLUMN product_categories_json TEXT DEFAULT '[]'",
    "ALTER TABLE content_items ADD COLUMN selected_for_fast_build INTEGER DEFAULT 0",
    "ALTER TABLE content_items ADD COLUMN processing_status TEXT DEFAULT 'pending'",
    "ALTER TABLE content_items ADD COLUMN processing_error TEXT",
    "ALTER TABLE content_items ADD COLUMN last_processed_at TEXT",
    "ALTER TABLE build_runs ADD COLUMN mode TEXT DEFAULT 'preview'",
    "ALTER TABLE build_runs ADD COLUMN requested_max_items INTEGER",
    "ALTER TABLE build_runs ADD COLUMN total_found INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN product_candidates_found INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN selected_for_fast_build INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN summarized_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN indexed_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN remaining_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN failed_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN skipped_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN background_enrichment_enabled INTEGER DEFAULT 1",
    "ALTER TABLE build_runs ADD COLUMN cancel_requested INTEGER DEFAULT 0",
    # product extraction + suggestions
    "ALTER TABLE content_items ADD COLUMN products_mentioned_json TEXT DEFAULT '[]'",
    "ALTER TABLE content_items ADD COLUMN brands_mentioned_json TEXT DEFAULT '[]'",
    # two-phase build
    "ALTER TABLE build_runs ADD COLUMN is_usable INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN first_usable_at TEXT",
    "ALTER TABLE build_runs ADD COLUMN enrichment_status TEXT DEFAULT ''",
    "ALTER TABLE build_runs ADD COLUMN scanned_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN suggested_products_count INTEGER DEFAULT 0",
    "ALTER TABLE build_runs ADD COLUMN estimated_time_to_usable_seconds INTEGER",
    "ALTER TABLE build_runs ADD COLUMN estimated_background_time_seconds INTEGER",
    "ALTER TABLE build_runs ADD COLUMN ai_calls_used INTEGER DEFAULT 0",
    # soft delete
    "ALTER TABLE creators ADD COLUMN deleted_at TEXT",
    "ALTER TABLE creators ADD COLUMN deleted_reason TEXT",
    # multi-platform source status
    "ALTER TABLE creator_profiles ADD COLUMN source_status TEXT DEFAULT 'found'",
]

SCHEMA += """
CREATE TABLE IF NOT EXISTS llm_usage_logs (
    id TEXT PRIMARY KEY,
    provider TEXT,
    model TEXT,
    task TEXT,
    creator_id TEXT,
    session_id TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS resolved_products (
    product_id TEXT PRIMARY KEY,
    source TEXT,
    input_url TEXT,
    canonical_url TEXT,
    asin TEXT,
    title TEXT,
    brand TEXT,
    price_text TEXT,
    price_cents INTEGER,
    currency TEXT DEFAULT 'USD',
    image_url TEXT,
    rating REAL,
    review_count INTEGER,
    availability TEXT,
    metadata_json TEXT DEFAULT '{}',
    metadata_source TEXT DEFAULT 'unknown',
    metadata_confidence REAL DEFAULT 0,
    price_verified_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS build_locks (
    lock_id TEXT PRIMARY KEY,
    creator_id TEXT,
    run_id TEXT,
    lock_type TEXT DEFAULT 'build',
    status TEXT DEFAULT 'active',
    acquired_at TEXT,
    heartbeat_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_locks_creator ON build_locks(creator_id, status);

CREATE TABLE IF NOT EXISTS product_take_sessions (
    session_id TEXT PRIMARY KEY,
    creator_id TEXT,
    current_product_json TEXT DEFAULT '{}',
    current_topic TEXT DEFAULT '',
    context_json TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS product_take_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT,
    role TEXT,
    content TEXT,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ptm_session ON product_take_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS suggested_products (
    suggested_product_id TEXT PRIMARY KEY,
    creator_id TEXT,
    product_name TEXT,
    product_brand TEXT,
    product_category TEXT,
    product_url TEXT,
    image_url TEXT,
    price_text TEXT,
    source_content_id TEXT,
    source_platform TEXT,
    source_title TEXT,
    source_url TEXT,
    reason_label TEXT,
    reason_detail TEXT,
    suggestion_type TEXT,
    confidence REAL DEFAULT 0,
    recency_score REAL DEFAULT 0,
    relevance_score REAL DEFAULT 0,
    final_score REAL DEFAULT 0,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_suggested_creator ON suggested_products(creator_id, final_score);
"""


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                db.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def upsert(db, table: str, key_col: str, row: dict):
    """Insert or update a row keyed on key_col."""
    row = dict(row)
    row.setdefault("updated_at", now())
    exists = db.execute(
        f"SELECT 1 FROM {table} WHERE {key_col}=?", (row[key_col],)
    ).fetchone()
    if exists:
        cols = [c for c in row if c != key_col]
        sets = ", ".join(f"{c}=?" for c in cols)
        db.execute(
            f"UPDATE {table} SET {sets} WHERE {key_col}=?",
            [row[c] for c in cols] + [row[key_col]],
        )
    else:
        row.setdefault("created_at", now())
        cols = list(row)
        db.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [row[c] for c in cols],
        )


def update_run(run_id: str, **fields):
    from .db_writer import write
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = [*fields.values(), run_id]
    write(lambda conn: conn.execute(f"UPDATE build_runs SET {sets} WHERE run_id=?", vals))


def add_run_error(run_id: str, error: str):
    with get_db() as db:
        row = db.execute("SELECT errors_json FROM build_runs WHERE run_id=?", (run_id,)).fetchone()
        errors = json.loads(row["errors_json"] or "[]") if row else []
        errors.append({"time": now(), "error": str(error)[:500]})
        db.execute("UPDATE build_runs SET errors_json=? WHERE run_id=?", (json.dumps(errors), run_id))


init_db()
