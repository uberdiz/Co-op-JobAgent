"""Remember which postings were already reported. Local file for dev, gs:// path in Cloud Run."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Set


def _split_gs(path: str):
    rest = path[len("gs://"):]
    bucket, _, blob = rest.partition("/")
    return bucket, blob


def read_text(path: str) -> str | None:
    if path.startswith("gs://"):
        from google.cloud import storage  # imported lazily so local runs/tests don't need credentials

        bucket, blob = _split_gs(path)
        b = storage.Client().bucket(bucket).blob(blob)
        return b.download_as_text() if b.exists() else None
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else None


def write_text(path: str, text: str) -> None:
    if path.startswith("gs://"):
        from google.cloud import storage

        bucket, blob = _split_gs(path)
        storage.Client().bucket(bucket).blob(blob).upload_from_string(text, content_type="text/plain; charset=utf-8")
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class SeenStore:
    def __init__(self, path: str):
        self.path = path
        raw = read_text(path)
        self.urls: Set[str] = set(json.loads(raw)) if raw else set()

    @staticmethod
    def norm(url: str) -> str:
        return url.strip().rstrip("/")

    def add(self, urls: Iterable[str]) -> None:
        self.urls.update(self.norm(u) for u in urls)

    def save(self) -> None:
        write_text(self.path, json.dumps(sorted(self.urls), indent=2))
