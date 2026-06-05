"""Universal connector interface.

Contract: a connector NEVER raises out of run(). It records its own
connector_runs row, saves what it can, and returns stats. One platform
failing must never block the others.
"""
import json
import logging

from ..db import get_db, new_id, now
from ..normalization.normalize_posts import save_comment, save_content_item
from ..normalization.normalize_profiles import save_profile

log = logging.getLogger("creator_twin.connectors")


class BaseConnector:
    platform = "base"

    def __init__(self, creator_id: str, config: dict = None, run_id: str = None, progress=None):
        self.creator_id = creator_id
        self.config = config or {}
        self.run_id = run_id
        self.progress = progress or (lambda msg: None)
        mi = self.config.get("max_items", 100)
        self.max_items = None if mi is None else int(mi)  # None = full catalog
        self.errors = []

    # ---- override these ----
    def validate_config(self) -> bool:
        """Return False (with self.errors appended) if this connector can't run."""
        return True

    def fetch_profile(self):
        """Return raw profile data or None."""
        return None

    def fetch_content(self) -> list:
        """Return list of raw content records."""
        return []

    def fetch_comments(self, normalized_items: list) -> list:
        """Return list of (content_id, raw_comment) tuples."""
        return []

    def normalize_profile(self, raw) -> dict:
        """Map raw profile -> creator_profiles columns dict."""
        return {}

    def normalize_content_item(self, raw) -> dict:
        """Map raw content -> content_items columns dict. Return None to skip."""
        return None

    def normalize_comments(self, raw) -> dict:
        """Map raw comment -> comments columns dict."""
        return raw

    # ---- orchestration ----
    def run(self) -> dict:
        crun_id = new_id("crn")
        with get_db() as db:
            db.execute(
                "INSERT INTO connector_runs (connector_run_id, run_id, creator_id, platform, status, started_at)"
                " VALUES (?,?,?,?,?,?)",
                (crun_id, self.run_id, self.creator_id, self.platform, "running", now()))
        stats = {"platform": self.platform, "status": "completed",
                 "items_found": 0, "items_saved": 0, "items_skipped": 0, "errors": []}
        try:
            if not self.validate_config():
                stats["status"] = "skipped"
                stats["errors"] = self.errors
                self._finish(crun_id, stats)
                return stats

            try:
                raw_profile = self.fetch_profile()
                if raw_profile:
                    save_profile(self.creator_id, self.platform, self.normalize_profile(raw_profile))
            except Exception as e:
                self._err(stats, f"profile: {e}")

            normalized = []
            try:
                raw_items = self.fetch_content()[: self.max_items]
                stats["items_found"] = len(raw_items)
                for raw in raw_items:
                    try:
                        item = self.normalize_content_item(raw)
                        if item is None:
                            stats["items_skipped"] += 1
                            continue
                        cid = save_content_item(self.creator_id, self.platform, item)
                        normalized.append({"content_id": cid, **item})
                        stats["items_saved"] += 1
                    except Exception as e:
                        stats["items_skipped"] += 1
                        self._err(stats, f"item: {e}")
                self.progress(f"{self.platform}: {stats['items_saved']} items saved")
            except Exception as e:
                self._err(stats, f"content: {e}")

            if not self.config.get("skip_comments"):
                try:
                    for content_id, raw_c in self.fetch_comments(normalized):
                        save_comment(content_id, self.platform, self.normalize_comments(raw_c))
                except Exception as e:
                    self._err(stats, f"comments: {e}")

            if stats["errors"] and not stats["items_saved"]:
                stats["status"] = "failed"
        except Exception as e:
            stats["status"] = "failed"
            self._err(stats, str(e))
        self._finish(crun_id, stats)
        return stats

    def _err(self, stats, msg):
        log.warning("[%s] %s", self.platform, str(msg)[:200])
        stats["errors"].append(str(msg)[:300])

    def _finish(self, crun_id, stats):
        with get_db() as db:
            db.execute(
                "UPDATE connector_runs SET status=?, finished_at=?, items_found=?, items_saved=?,"
                " items_skipped=?, errors_json=? WHERE connector_run_id=?",
                (stats["status"], now(), stats["items_found"], stats["items_saved"],
                 stats["items_skipped"], json.dumps(stats["errors"]), crun_id))
