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

    python -m tools.cli dataset [--check]
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import archive, edition

DATASET_PATH = Path("dataset/notices.jsonl")
SCHEMA_PATH = Path("dataset/schema.md")

Record = dict[str, Any]


def _notice_text(notice: Record | None, ledger: Record | None) -> tuple[str, list[Record]]:
    """Clean text for one Dashen notice, plus per-image section records."""
    if not notice:
        return "", []
    overrides = archive.corrected_blocks(notice, ledger)
    inserts = archive.insert_lines(ledger)
    sections: list[Record] = []
    all_lines: list[str] = []
    for image in notice.get("images", []):
        index = image["index"]
        texts: list[str] = []
        previous = ""
        image_inserts = inserts.get(index, [])
        for block_index, block in enumerate(image.get("blocks", [])):
            locator = (index, block_index)
            if locator in overrides:
                replacement = overrides[locator]
                text = None if replacement is None else replacement
            else:
                text = block.get("text") or ""
            if text is not None:
                cleaned = edition.clean_block(text)
                if cleaned and cleaned != previous:
                    texts.append(cleaned)
                    previous = cleaned
            for after, extra in image_inserts:
                if after != block_index:
                    continue
                cleaned_extra = edition.clean_block(extra)
                if cleaned_extra and cleaned_extra != previous:
                    texts.append(cleaned_extra)
                    previous = cleaned_extra
        if texts:
            sections.append({"image": index, "lines": texts})
            all_lines.extend(texts)
    return "\n".join(all_lines), sections


def _nga_text(post: Record) -> str:
    return edition.normalize_body(post.get("text") or "")


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


def build(root: str | Path | None = None) -> list[Record]:
    """One dataset row per notice, sorted by published_at then id."""
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
                if quality:
                    row["quality"] = quality
            rows.append(row)
    rows.sort(key=lambda row: (row["published_at"], row["id"]))
    by_date: dict[str, list[Record]] = {}
    for row in rows:
        by_date.setdefault(row["notice_date"], []).append(row)
    for group in by_date.values():
        preferred = "nga" if any(row["source"] == "nga" for row in group) else "dashen"
        for row in group:
            row["canonical"] = row["source"] == preferred
    return rows


def serialize(rows: list[Record]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def run(root: str | Path | None = None, *, check: bool = False) -> int:
    import sys

    base = Path(root) if root else archive.REPO_ROOT
    rows = build(base)
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
