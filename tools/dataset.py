"""The website-facing dataset: one clean JSON row per notice.

``dataset/notices.jsonl`` carries every notice from both sources (one row
each, 220 rows), with the canonical flag, the full cleaned text, and the
audit quality summary:

* Dashen text = OCR blocks + correction ledger (corrections, drops,
  inserts), watermark cleaned, line breaks preserved, grouped by image;
* NGA text = the repost's own plain text, whitespace-normalised;
* ``canonical`` marks the preferred reading per notice date (NGA when both
  cover the date, otherwise Dashen);
* ``quality`` summarises the audit ledger for the notice.

A Dashen row is only publishable when its audit quality exists, has no
``queued`` blocks and is ``verified``. The gate raises :class:`DatasetError`
instead of writing an unreviewed row; ``--allow-unverified`` is the explicit
override for a deliberate snapshot. NGA rows are plain source text and are
not gated.

    python -m tools.cli dataset [--check] [--allow-unverified]
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import archive, projection
from .text import normalize_body

DATASET_PATH = Path("dataset/notices.jsonl")

Record = dict[str, Any]


class DatasetError(RuntimeError):
    """A Dashen row lacks verified audit quality, so it must not be published."""


def _notice_text(notice: Record | None, ledger: Record | None) -> tuple[str, list[Record]]:
    """Clean text for one Dashen notice, plus per-image section records."""
    if not notice:
        return "", []
    sections = projection.effective_sections(notice, ledger)
    rows = [{"image": index, "lines": lines} for index, lines in sections]
    return "\n".join(line for _, lines in sections for line in lines), rows


def _nga_text(post: Record) -> str:
    return normalize_body(post.get("text") or "")


def _quality(feed: str, base: Path) -> Record | None:
    path = base / "derived" / "audit" / "dashen" / f"{feed}.json"
    if not path.exists():
        return None
    audit = json.loads(path.read_text(encoding="utf-8"))
    counts = audit.get("counts", {})
    return {
        "audit": audit.get("engine_audit"),
        "blocks": sum(v for k, v in counts.items() if k != "queued"),
        "queued": counts.get("queued", 0),
        "consensus": counts.get("consensus", 0),
        "corrected": counts.get("corrected", 0) + counts.get("punct_rule", 0),
        "adjudicated": counts.get("adjudicated", 0) + counts.get("record", 0)
        + counts.get("source_issue", 0),
        "watermark_blocks": counts.get("watermark", 0),
        "dropped_blocks": counts.get("dropped", 0),
        "inserted_lines": len(audit.get("inserts", [])),
        "status": "verified" if counts.get("queued", 0) == 0 else "in_progress",
    }


def _require_verified(feed: str, quality: Record | None) -> None:
    """Refuse to publish a Dashen row that the audit has not verified."""
    if quality is None:
        raise DatasetError(
            f"unverified Dashen notice {feed}: no audit quality "
            f"(run `python -m tools.cli audit-status`, or pass --allow-unverified)"
        )
    if quality["queued"]:
        raise DatasetError(
            f"unverified Dashen notice {feed}: {quality['queued']} queued audit "
            f"blocks (run audit-status, or pass --allow-unverified)"
        )
    if quality["status"] != "verified":
        raise DatasetError(
            f"unverified Dashen notice {feed}: audit status is "
            f"{quality['status']!r} (pass --allow-unverified to publish anyway)"
        )


def build(
    root: str | Path | None = None, *, allow_unverified: bool = False
) -> list[Record]:
    """One dataset row per notice, sorted by published_at then id.

    Dashen rows require verified audit quality unless ``allow_unverified``.
    """
    base = Path(root) if root else archive.REPO_ROOT
    rows: list[Record] = []
    for source in archive.SOURCES.values():
        if not (base / source.corpus).exists() or not (base / source.selection).exists():
            continue
        for post, selection in archive.selected_posts(base, "update", source.name):
            feed = post["id"]
            notice = archive.load_ocr(feed, base) if source.ocr else None
            ledger = archive.load_ledger(feed, base) if source.ocr else None
            if source.ocr:
                text, sections = _notice_text(notice, ledger)
            else:
                text = _nga_text(post)
                sections = []
            images = [image["path"] for image in (notice or {}).get("images", []) if image.get("path")]
            row: Record = {
                "id": archive.notice_id(source.name, feed),
                "source": source.name,
                "canonical": None,  # filled after all rows exist
                "title": post["title"],
                "notice_date": selection["notice_date"],
                "published_at": post["published_at"],
                "url": archive.post_url(post),
                "source_author_uid": post.get("author_uid"),
                "images": images,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            if sections:
                row["sections"] = sections
            if source.ocr:
                quality = _quality(feed, base)
                if not allow_unverified:
                    _require_verified(feed, quality)
                if quality:
                    row["quality"] = quality
            rows.append(row)
    rows.sort(key=lambda row: (row["published_at"], row["id"]))
    projection.assign_canonical(rows)
    return rows


def serialize(rows: list[Record]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def run(
    root: str | Path | None = None,
    *,
    check: bool = False,
    allow_unverified: bool = False,
) -> int:
    import sys

    base = Path(root) if root else archive.REPO_ROOT
    rows = build(base, allow_unverified=allow_unverified)
    payload = serialize(rows)
    out = base / DATASET_PATH
    if check:
        if not out.exists() or out.read_text(encoding="utf-8") != payload:
            print("dataset: stale dataset/notices.jsonl", file=sys.stderr)
            return 1
        print(f"dataset: {len(rows)} rows, up to date")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8", newline="\n")
    canonical = sum(1 for row in rows if row["canonical"])
    print(f"dataset: wrote {len(rows)} rows ({canonical} canonical) to {DATASET_PATH}")
    return 0
