"""Uploaded files connector — creator-provided PDFs/docs/markdown/text/CSVs.

source_type='creator_uploaded', confidence='high'. The highest-trust source.
"""
import csv
import html as html_mod
import logging
import re
from pathlib import Path

from .base import BaseConnector

log = logging.getLogger("creator_twin.connectors.uploads")
TEXT_EXTS = {".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".json", ".vtt", ".srt"}


def read_file_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except ImportError:
            log.warning("pypdf not installed — skipping %s (pip install pypdf)", path.name)
            return ""
        except Exception as e:
            log.warning("PDF parse failed %s: %s", path.name, e)
            return ""
    if ext not in TEXT_EXTS:
        return ""
    try:
        raw = path.read_text(errors="ignore")
    except Exception:
        return ""
    if ext in (".html", ".htm"):
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
        raw = html_mod.unescape(re.sub(r"<[^>]+>", " ", raw))
    if ext == ".csv":
        try:
            rows = list(csv.reader(raw.splitlines()))[:300]
            raw = "\n".join(", ".join(r) for r in rows)
        except Exception:
            pass
    if ext in (".vtt", ".srt"):
        raw = re.sub(r"\d{2}:\d{2}[:.,\d]*\s*(-->.*)?", "", raw)
    return re.sub(r"\s+", " ", raw).strip()


class UploadedFilesConnector(BaseConnector):
    platform = "uploads"

    def validate_config(self):
        d = self.config.get("files_dir")
        if not d or not Path(d).exists():
            self.errors.append("no uploaded-files dir provided or it does not exist")
            return False
        return True

    def fetch_content(self):
        files = []
        for path in sorted(Path(self.config["files_dir"]).rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                text = read_file_text(path)
                if len(text) > 50:
                    files.append({"path": path, "text": text})
        return files

    def normalize_content_item(self, f):
        path = f["path"]
        complete = len(f["text"]) > 400
        return {
            "platform_content_id": re.sub(r"\W+", "_", path.name)[:120],
            "canonical_url": "",
            "content_type": "uploaded_document",
            "title": path.stem.replace("_", " ").replace("-", " ")[:200],
            "text_body": f["text"][:200000],
            "source_type": "creator_uploaded",
            "confidence": "high" if complete else "medium",
            "raw": {"filename": path.name},
        }
