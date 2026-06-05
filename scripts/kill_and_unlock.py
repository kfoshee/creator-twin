"""Dev cleanup: stop runaway build processes and release stale DB locks.

Usage: python scripts/kill_and_unlock.py
"""
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from creator_twin.config import DB_PATH
from creator_twin.db import get_db, now

TARGETS = ("fast_build.py", "background_worker.py", "ingest.py")


def kill_processes():
    out = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True).stdout
    me = os.getpid()
    killed = []
    for line in out.splitlines():
        if any(t in line for t in TARGETS) and "kill_and_unlock" not in line:
            pid = int(line.strip().split()[0])
            if pid == me:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append((pid, line.strip()[:90]))
            except ProcessLookupError:
                pass
    return killed


def main():
    print(f"DB path: {DB_PATH}")
    with get_db() as db:
        active = [dict(r) for r in db.execute(
            "SELECT run_id, status, step, started_at FROM build_runs WHERE status IN ('running','enriching')").fetchall()]
    print(f"Active builds before: {active or 'none'}")

    for pid, cmd in kill_processes():
        print(f"  stopped pid {pid}: {cmd}")

    with get_db() as db:
        n1 = db.execute("UPDATE build_runs SET status='stopped', step_detail='cleared by kill_and_unlock', finished_at=? "
                        "WHERE status IN ('running','enriching')", (now(),)).rowcount
        n2 = db.execute("UPDATE build_locks SET status='released' WHERE status='active'").rowcount
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(f"Marked {n1} stale build_runs stopped; released {n2} locks; WAL checkpointed.")

    with get_db() as db:
        active = [dict(r) for r in db.execute(
            "SELECT run_id, status FROM build_runs WHERE status IN ('running','enriching')").fetchall()]
    print(f"Active builds after: {active or 'none'}")


if __name__ == "__main__":
    main()
