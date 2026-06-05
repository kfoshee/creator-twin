"""Newsletter connector — uploaded exports (HTML/MD/TXT/CSV, one issue per file).

source_type='uploaded_export'.
"""
import logging
import re
from pathlib import Path

from .base import BaseConnector
from .uploaded_files import read_file_text

log = logging.getLogger("creator_twin.connectors.newsletter")


class NewsletterConnector(BaseConnector):
    platform = "newsletter"

    def validate_config(self):
        d = self.config.get("export_dir")
        if not d or not Path(d).exists():
            self.errors.append("no newsletter export dir provided")
            return False
        return True

    def fetch_content(self):
        issues = []
        for path in sorted(Path(self.config["export_dir"]).rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                text = read_file_text(path)
                if len(text) > 100:
                    # try to recover a date from the filename (2024-01-15-subject.md style)
                    m = re.search(r"(\d{4}[-_]\d{2}[-_]\d{2})", path.name)
                    issues.append({"path": path, "text": text,
                                   "date": m.group(1).replace("_", "-") if m else ""})
        return issues

    def normalize_content_item(self, issue):
        path = issue["path"]
        return {
            "platform_content_id": re.sub(r"\W+", "_", path.name)[:120],
            "content_type": "newsletter_issue",
            "title": path.stem.replace("_", " ").replace("-", " ")[:200],
            "text_body": issue["text"][:200000],
            "published_at": issue["date"],
            "source_type": "uploaded_export",
            "confidence": "high",
            "raw": {"filename": path.name},
        }
