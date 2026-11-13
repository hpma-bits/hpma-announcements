"""Apply audit adjudication verdicts to the correction ledger.

Reads every ``.review/audit/verdicts-*.jsonl`` file and translates verdicts
into ledger entries, idempotently (locators already present are skipped):

* ``correct``        -> block correction (original = raw OCR text);
* ``drop``           -> block drop verdict;
* ``v4_ok`` / ``v4_right`` / ``source_issue`` / ``v6_garbage`` /
  ``v4_covered`` / ``cover_art`` -> no ledger change (audit status only);
* ``v6_right``       -> treated like ``correct`` with corrected = v6 text;
* ``missed_line``    -> NOT applied here; the coordinator turns these into
  ledger insert entries with a verified anchor block.

    python -m tools.cli apply-verdicts
"""

from __future__ import annotations

import json
from pathlib import Path

from . import archive

VERDICT_DIR = Path(".review/audit")
LEDGER_DIR = Path("corrections/dashen")

APPLY_AS_CORRECTION = {"correct", "v6_right"}
APPLY_AS_DROP = {"drop"}
NO_CHANGE = {"v4_ok", "v4_right", "source_issue", "v6_garbage", "v4_covered",
             "cover_art", "record", "revert", "keep", "confirm", "modify"}


def run(root: str | Path | None = None) -> int:
    import sys

    base = Path(root) if root else archive.REPO_ROOT
    stats = {"files": 0, "applied": 0, "skipped_present": 0,
             "no_change": 0, "missed_line": 0, "undecided": 0}
    verdict_files = sorted((base / VERDICT_DIR).glob("verdicts-*.jsonl"))
    for path in verdict_files:
        stats["files"] += 1
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
        by_notice: dict[str, list[dict]] = {}
        for row in rows:
            by_notice.setdefault(row["notice"], []).append(row)
        for feed, items in by_notice.items():
            ocr = archive.read_json(base / "derived" / "ocr" / "dashen" / f"{feed}.json")
            ledger_path = base / LEDGER_DIR / f"{feed}.json"
            ledger = archive.read_json(ledger_path) if ledger_path.exists() else None
            corrections = ledger.get("corrections", []) if ledger else []
            have = {(c["image_index"], c.get("block_index")) for c in corrections
                    if not c.get("insert")}
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
                    continue
                locator = (row["image_index"], row["v4_block"])
                if locator in have:
                    stats["skipped_present"] += 1
                    continue
                if verdict in APPLY_AS_CORRECTION:
                    corrected = row.get("corrected")
                    if not corrected:
                        print(f"apply-verdicts: {feed} {locator} {verdict} without corrected text",
                              file=sys.stderr)
                        continue
                    raw = ocr["images"][locator[0] - 1]["blocks"][locator[1]].get("text") or ""
                    if corrected == raw:
                        stats["no_change"] += 1
                        continue
                    corrections.append({
                        "image_index": locator[0], "block_index": locator[1],
                        "original": raw, "corrected": corrected,
                        "reason": row.get("reason") or "audit adjudication",
                        "confidence": row.get("confidence") or "high",
                        "via": "audit-adjudication",
                        "recrop": f"python -m tools.cli crop dashen.{feed} {locator[0]} {locator[1]}",
                    })
                    have.add(locator)
                    stats["applied"] += 1
                    dirty = True
                elif verdict in APPLY_AS_DROP:
                    raw = ocr["images"][locator[0] - 1]["blocks"][locator[1]].get("text") or ""
                    corrections.append({
                        "image_index": locator[0], "block_index": locator[1],
                        "original": raw, "drop": True,
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
            if dirty:
                if ledger is None:
                    ledger = {"id": f"dashen.{feed}",
                              "source_ocr": f"derived/ocr/dashen/{feed}.json",
                              "verified_by": "main-session",
                              "verification": "audit adjudication verdicts with per-item crops",
                              "corrections": corrections}
                ledger["corrections"] = corrections
                ledger_path.write_text(
                    json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"apply-verdicts: {json.dumps(stats, sort_keys=True)}")
    return 0
