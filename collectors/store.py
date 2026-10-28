"""Deterministic on-disk store for update notices.

Layout::

    data/<source>/<year>/<native_id>.json
    index.json

Collectors produce :class:`collectors.model.Notice` objects. The store owns
file layout, revisioning, the index and schema validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .model import Notice

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "notice.schema.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def compute_content_hash(item: Notice) -> str:
    """Hash source-derived content only.

    Local archive state (path, bytes, dimensions, first_seen_at, revision) is
    excluded so downloading images later does not fabricate a revision.
    """
    payload = {
        "title": item.title,
        "text": item.text,
        "published_at": item.published_at,
        "source_updated_at": item.source_updated_at,
        "notice_date": item.notice_date,
        "images": [{"index": image.index, "url": image.url} for image in item.images],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


@dataclass
class WriteResult:
    action: str  # created | updated | unchanged
    path: str
    revision: int
    item_id: str


class Store:
    def __init__(self, root: str | Path = REPO_ROOT) -> None:
        self.root = Path(root).resolve()
        self.data_dir = self.root / "data"
        self.index_path = self.root / "index.json"
        local_schema = self.root / "schema" / "notice.schema.json"
        self.schema_path = local_schema if local_schema.exists() else SCHEMA_PATH

    def path_for(self, item: Notice) -> Path:
        source, _, native_id = item.id.partition(".")
        if not source or not native_id:
            raise ValueError("Notice.id must be '<source>.<native_id>'")
        year = item.published_at[:4]
        if not year.isdigit():
            raise ValueError(f"Notice.published_at must start with a year: {item.published_at!r}")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", native_id).strip("-")
        if not safe:
            raise ValueError(f"Notice.id has no usable native id: {item.id!r}")
        return self.data_dir / source / year / f"{safe}.json"

    def write(self, item: Notice) -> WriteResult:
        if not item.id or not item.title:
            raise ValueError("Notice.id and Notice.title are required")

        path = self.path_for(item)
        existing: dict[str, Any] | None = None
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))

        new_hash = compute_content_hash(item)
        if existing and existing.get("content_hash") == new_hash:
            return WriteResult(
                "unchanged",
                path.relative_to(self.root).as_posix(),
                int(existing["revision"]),
                item.id,
            )

        now = utc_now()
        if existing:
            item.revision = int(existing["revision"]) + 1
            item.first_seen_at = existing.get("first_seen_at") or now
        else:
            item.revision = 1
            item.first_seen_at = now
        item.content_hash = new_hash

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(dumps(item.to_dict()), encoding="utf-8")
        os.replace(tmp, path)
        return WriteResult(
            "updated" if existing else "created",
            path.relative_to(self.root).as_posix(),
            item.revision,
            item.id,
        )

    def iter_notices(self) -> Iterable[dict[str, Any]]:
        if not self.data_dir.exists():
            return
        for path in sorted(self.data_dir.rglob("*.json")):
            yield json.loads(path.read_text(encoding="utf-8"))

    def write_index(self) -> Path:
        items = list(self.iter_notices())
        items.sort(key=lambda item: (item["published_at"], item["id"]), reverse=True)
        payload = {
            "count": len(items),
            "items": [
                {
                    "id": item["id"],
                    "url": item["url"],
                    "published_at": item["published_at"],
                    "notice_date": item["notice_date"],
                    "title": item["title"],
                    "image_count": len(item.get("images") or []),
                    "revision": item["revision"],
                }
                for item in items
            ],
        }
        text = dumps(payload)
        if not self.index_path.exists() or self.index_path.read_text(encoding="utf-8") != text:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text(text, encoding="utf-8")
        return self.index_path

    def validate(self) -> list[str]:
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        errors: list[str] = []
        seen: set[str] = set()
        paths = sorted(self.data_dir.rglob("*.json")) if self.data_dir.exists() else []
        for path in paths:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{path}: cannot read JSON ({exc})")
                continue
            for error in sorted(
                validator.iter_errors(item),
                key=lambda err: tuple(str(part) for part in err.path),
            ):
                errors.append(f"{path}: {error.message}")
            if isinstance(item, dict):
                item_id = item.get("id")
                if isinstance(item_id, str):
                    if item_id in seen:
                        errors.append(f"{path}: duplicate id {item_id}")
                    seen.add(item_id)
        return errors
