"""Apply audit adjudication verdicts to the correction ledger.

Reads every tracked ``audit/verdicts-*.jsonl`` file — the evidence committed
with the audit record — and translates verdicts into ledger entries,
idempotently (locators already present are skipped). The gitignored
``.review/`` workspace is never read.

* ``correct`` / ``v6_right`` -> block correction (original = current OCR text);
* ``drop``                  -> block drop verdict;
* ``v4_ok`` / ``v4_right`` / ``source_issue`` / ``v6_garbage`` /
  ``v4_covered`` / ``cover_art`` -> no ledger change (audit status only);
* ``missed_line``           -> NOT applied here; the coordinator turns these
  into ledger insert entries with a verified anchor block.

Every verdict is validated against the current OCR record before it can touch
a ledger: the locator must be in range, and a row carrying ``original`` or
``v4_text`` must match the block text the OCR actually holds. Malformed rows,
stale texts and missing notices are reported and skipped; one bad entry never
aborts the run. Ledgers are replaced atomically.

    python -m tools.cli apply-verdicts [--dry-run]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from collectors import atomic

from . import archive

VERDICT_DIR = Path("audit")
LEDGER_DIR = Path("corrections/dashen")

APPLY_AS_CORRECTION = {"correct", "v6_right"}
APPLY_AS_DROP = {"drop"}

Record = dict[str, Any]


def _rows(path: Path) -> Iterator[tuple[int, Record | None, str | None]]:
    """Yield ``(line_number, row, problem)``; bad JSON becomes a problem."""
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            yield number, None, f"invalid JSON: {exc.msg}"
            continue
        if not isinstance(row, dict):
            yield number, None, "verdict row is not a JSON object"
            continue
        if not isinstance(row.get("notice"), str) or not row["notice"]:
            yield number, None, "verdict row has no notice id"
            continue
        yield number, row, None


def _block_text(
    notice: Record, image_index: Any, block_index: Any
) -> tuple[str | None, str | None]:
    """Current OCR text at a locator, or ``(None, problem)`` when out of range."""
    images = notice.get("images") or []
    if not isinstance(image_index, int) or not isinstance(block_index, int):
        return None, "image_index/v4_block must be integers"
    if not 1 <= image_index <= len(images):
        return None, f"image_index {image_index} out of range (notice has {len(images)} images)"
    blocks = images[image_index - 1].get("blocks") or []
    if not 0 <= block_index < len(blocks):
        return None, (
            f"block {block_index} out of range "
            f"(image {image_index} has {len(blocks)} blocks)"
        )
    return blocks[block_index].get("text") or "", None


def run(root: str | Path | None = None, *, dry_run: bool = False) -> int:
    import sys

    base = Path(root) if root else archive.REPO_ROOT
    stats = {
        "files": 0,
        "notices": 0,
        "applied": 0,
        "ledgers": 0,
        "skipped_present": 0,
        "skipped_no_v4": 0,
        "no_change": 0,
        "missed_line": 0,
        "undecided": 0,
        "invalid": 0,
    }
    by_notice: dict[str, list[Record]] = {}
    for path in sorted((base / VERDICT_DIR).glob("verdicts-*.jsonl")):
        stats["files"] += 1
        for number, row, problem in _rows(path):
            if problem:
                stats["invalid"] += 1
                print(f"apply-verdicts: {path.name}:{number}: {problem}", file=sys.stderr)
                continue
            by_notice.setdefault(row["notice"], []).append(row)

    for feed in sorted(by_notice):
        items = by_notice[feed]
        stats["notices"] += 1
        try:
            notice = archive.load_ocr(feed, base)
        except (OSError, ValueError) as exc:
            stats["invalid"] += len(items)
            print(
                f"apply-verdicts: {feed}: cannot read OCR ({exc}); "
                f"skipped {len(items)} verdict(s)",
                file=sys.stderr,
            )
            continue
        ledger_path = base / LEDGER_DIR / f"{feed}.json"
        try:
            ledger = archive.read_json(ledger_path) if ledger_path.exists() else None
        except (OSError, ValueError) as exc:
            stats["invalid"] += len(items)
            print(
                f"apply-verdicts: {feed}: cannot read ledger ({exc}); "
                f"skipped {len(items)} verdict(s)",
                file=sys.stderr,
            )
            continue
        corrections = list((ledger or {}).get("corrections", []))
        have = {
            (correction["image_index"], correction.get("block_index"))
            for correction in corrections
            if not correction.get("insert")
        }
        dirty = False
        for row in items:
            verdict = row.get("verdict")
            if verdict == "missed_line":
                stats["missed_line"] += 1
                continue
            if verdict == "undecided":
                stats["undecided"] += 1
                continue
            if row.get("v4_block") is None:
                stats["skipped_no_v4"] += 1
                continue

            locator = (row["image_index"], row["v4_block"])
            raw, problem = _block_text(notice, *locator)
            if problem:
                stats["invalid"] += 1
                print(f"apply-verdicts: {feed} {locator}: {problem}", file=sys.stderr)
                continue
            expected = row.get("original")
            if expected is None:
                expected = row.get("v4_text")
            if expected is not None and expected != raw:
                stats["invalid"] += 1
                print(
                    f"apply-verdicts: {feed} {locator}: verdict text no longer "
                    f"matches OCR (verdict={expected!r}, ocr={raw!r})",
                    file=sys.stderr,
                )
                continue
            if locator in have:
                stats["skipped_present"] += 1
                continue

            if verdict in APPLY_AS_CORRECTION:
                corrected = row.get("corrected")
                if not isinstance(corrected, str) or not corrected:
                    stats["invalid"] += 1
                    print(
                        f"apply-verdicts: {feed} {locator}: {verdict} without "
                        f"corrected text",
                        file=sys.stderr,
                    )
                    continue
                if corrected == raw:
                    stats["no_change"] += 1
                    continue
                corrections.append({
                    "image_index": locator[0],
                    "block_index": locator[1],
                    "original": raw,
                    "corrected": corrected,
                    "reason": row.get("reason") or "audit adjudication",
                    "confidence": row.get("confidence") or "high",
                    "via": "audit-adjudication",
                    "recrop": f"python -m tools.cli crop dashen.{feed} {locator[0]} {locator[1]}",
                })
                have.add(locator)
                stats["applied"] += 1
                dirty = True
            elif verdict in APPLY_AS_DROP:
                corrections.append({
                    "image_index": locator[0],
                    "block_index": locator[1],
                    "original": raw,
                    "drop": True,
                    "reason": row.get("reason") or "audit adjudication: 重复切片/无独立源行",
                    "confidence": row.get("confidence") or "high",
                    "via": "audit-adjudication",
                    "recrop": f"python -m tools.cli crop dashen.{feed} {locator[0]} {locator[1]}",
                })
                have.add(locator)
                stats["applied"] += 1
                dirty = True
            else:
                stats["no_change"] += 1

        if not dirty:
            continue
        stats["ledgers"] += 1
        if dry_run:
            continue
        if ledger is None:
            ledger = {
                "id": f"dashen.{feed}",
                "source_ocr": f"derived/ocr/dashen/{feed}.json",
                "verified_by": "main-session",
                "verification": "audit adjudication verdicts with per-item crops",
            }
        ledger["corrections"] = corrections
        atomic.write_text(
            ledger_path, json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
        )

    print(f"apply-verdicts: {json.dumps(stats, sort_keys=True)}")
    return 0
