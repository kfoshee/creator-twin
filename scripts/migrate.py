"""Initialize/migrate the database (runs at deploy time on hosted backends)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.config import DB_PATH
from creator_twin.db import init_db

init_db()
print(f"Database ready at {DB_PATH}")
