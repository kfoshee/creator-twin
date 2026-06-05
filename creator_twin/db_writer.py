"""Single-writer queue for local SQLite.

All hot-path writes (classifier batches, progress updates, enrichment) go
through one writer thread executing short transactions sequentially, so
concurrent build/enrichment/API threads can never deadlock each other.
Reads stay direct via db.get_db().

Usage:
    from .db_writer import write
    write(lambda conn: conn.execute("UPDATE ...", (...)))            # async
    write(fn, wait=True)                                            # block until done
"""
import logging
import queue
import random
import sqlite3
import threading
import time

from .db import get_db

log = logging.getLogger("creator_twin.db_writer")

_QUEUE: "queue.Queue" = queue.Queue()
_started = False
_lock = threading.Lock()


def _writer_loop():
    while True:
        fn, done, holder = _QUEUE.get()
        if fn is None:  # shutdown sentinel
            break
        for attempt in range(6):
            try:
                with get_db() as conn:  # short transaction; commits on exit
                    holder["result"] = fn(conn)
                holder["error"] = None
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    delay = min(0.2 * (2 ** attempt), 5) + random.random() * 0.2
                    log.warning("writer retry %d (db busy), sleeping %.1fs", attempt + 1, delay)
                    time.sleep(delay)
                    holder["error"] = e
                else:
                    holder["error"] = e
                    break
            except Exception as e:
                holder["error"] = e
                break
        if done:
            done.set()
        _QUEUE.task_done()


def _ensure_started():
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_writer_loop, daemon=True, name="db-writer").start()
            _started = True


def write(fn, wait: bool = False, timeout: float = 60):
    """Enqueue a write callable(conn). wait=True blocks for the result."""
    _ensure_started()
    done = threading.Event() if wait else None
    holder = {"result": None, "error": RuntimeError("not executed")}
    _QUEUE.put((fn, done, holder))
    if wait:
        if not done.wait(timeout):
            raise TimeoutError("db write timed out")
        if holder["error"]:
            raise holder["error"]
        return holder["result"]
    return None


def queue_length() -> int:
    return _QUEUE.qsize()


def flush(timeout: float = 30):
    """Wait for all queued writes to land (e.g., before stop/shutdown)."""
    _ensure_started()
    deadline = time.time() + timeout
    while not _QUEUE.empty() and time.time() < deadline:
        time.sleep(0.05)
