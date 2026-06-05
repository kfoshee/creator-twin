"""Central config for Creator Twin Fast Build."""
import os
from pathlib import Path

# Load .env if present (no python-dotenv dependency required)
ROOT = Path(__file__).resolve().parent.parent
_env_file = ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
PROFILE_DIR = DATA_DIR / "profiles"
DB_PATH = DATA_DIR / "creator_twin.db"
for d in (DATA_DIR, CACHE_DIR, PROFILE_DIR):
    d.mkdir(parents=True, exist_ok=True)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Optional platform tokens — connectors degrade gracefully without them
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_ITEMS_PER_PLATFORM = 100
DEEP_PASS_PER_PLATFORM = 25
WEBSITE_MAX_PAGES = 25

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Folders from legacy companion projects whose transcripts we preserve/reuse.
LEGACY_TRANSCRIPT_DIRS = [
    ROOT / "tony-companion" / "data" / "transcripts",
    ROOT / "lontv-companion" / "data" / "transcripts",
]

TRANSCRIPT_TIMEOUT_SECONDS = 30   # never wait longer than this per video
TRANSCRIPT_WORKERS = 4
DEFAULT_MAX_VIDEOS = 200
DEFAULT_DEEP_PASS = 40
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150

PERSONA_MODES = ("first_person_creator_take", "companion", "first_person_draft", "strict_cited_answer")
DEFAULT_PERSONA_MODE = "first_person_creator_take"
