"""Build lock system: one active build per creator, stale locks auto-release."""
import datetime
import logging

from .db import get_db, new_id, now
from .db_writer import write

log = logging.getLogger("creator_twin.locks")
STALE_MINUTES = 10


def _is_stale(ts: str) -> bool:
    try:
        t = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        return (datetime.datetime.now() - t).total_seconds() > STALE_MINUTES * 60
    except Exception:
        return True


def acquire(creator_id: str, run_id: str, lock_type: str = "build"):
    """Returns (lock_id, None) or (None, existing_run_id) if a fresh build holds it."""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM build_locks WHERE creator_id=? AND status='active'",
            (creator_id,)).fetchone()
    if row:
        if not _is_stale(row["heartbeat_at"] or row["acquired_at"]):
            return None, row["run_id"]
        release(row["lock_id"])  # stale — take over
    lock_id = new_id("lk")
    write(lambda c: c.execute(
        "INSERT INTO build_locks (lock_id, creator_id, run_id, lock_type, status, acquired_at, heartbeat_at)"
        " VALUES (?,?,?,?,?,?,?)", (lock_id, creator_id, run_id, lock_type, "active", now(), now())),
        wait=True)
    return lock_id, None


def heartbeat(lock_id: str):
    write(lambda c: c.execute("UPDATE build_locks SET heartbeat_at=? WHERE lock_id=?", (now(), lock_id)))


def release(lock_id: str):
    write(lambda c: c.execute("UPDATE build_locks SET status='released' WHERE lock_id=?", (lock_id,)), wait=True)


def downgrade(lock_id: str):
    """Build finished its first-usable phase; lock continues for enrichment."""
    write(lambda c: c.execute("UPDATE build_locks SET lock_type='enrichment', heartbeat_at=? WHERE lock_id=?",
                              (now(), lock_id)))


def cleanup_stale(max_run_minutes: int = 30) -> dict:
    """Startup hygiene: mark dead runs stopped, release stale locks."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(minutes=max_run_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    stats = {"runs_stopped": 0, "locks_released": 0}

    def apply(conn):
        cur = conn.execute(
            "UPDATE build_runs SET status='stopped', step_detail='stale (server restart)' "
            "WHERE status IN ('running','enriching') AND started_at < ?", (cutoff,))
        stats["runs_stopped"] = cur.rowcount
        rows = conn.execute("SELECT lock_id, heartbeat_at, acquired_at FROM build_locks WHERE status='active'").fetchall()
        for r in rows:
            if _is_stale(r["heartbeat_at"] or r["acquired_at"]):
                conn.execute("UPDATE build_locks SET status='released' WHERE lock_id=?", (r["lock_id"],))
                stats["locks_released"] += 1
    write(apply, wait=True)
    if stats["runs_stopped"] or stats["locks_released"]:
        log.info("startup cleanup: %s", stats)
    return stats
