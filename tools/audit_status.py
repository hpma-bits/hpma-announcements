"""Audit ledger: the per-block quality status of the v4 record.

Combines every evidence source into one status per OCR block, comparing the
v4 record against the v6 layer FRESH each run (no stale alignment files):

* ``consensus``    - v4 and v6 agree (equal, merged/split, or a punctuation
  pattern rule confirmed v4);
* ``punct_rule``   - the block's text was set by a ratified punctuation rule
  (ledger entry with ``via: punct-rule``);
* ``corrected``    - a human ledger correction (audit disagreement cannot
  outweigh a crop-verified decision);
* ``adjudicated``  - an individually reviewed audit item (verdict file);
* ``record``       - v4-only block whose line was verified against the source
  during adjudication (the v6 engine missed it);
* ``dropped``      - ledger drop (not edition text);
* ``watermark``    - platform watermark only (not edition text);
* ``source_issue`` - reviewed and kept as printed;
* ``queued``       - not yet adjudicated; the quality gate requires zero.

Blocks the v6 engine read but the v4 record has none of (``v6_only``) carry
no v4 block, so they are tracked separately as ``v6_only`` entries with their
own verdict status.

    python -m tools.cli audit-status [--soft]

Writes ``derived/audit/dashen/<feed_id>.json`` and prints totals. Exits 1
when ``queued`` blocks remain, unless ``--soft``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import archive, audit_compare, edition

AUDIT_DIR = Path("derived/audit/dashen")

Record = dict[str, Any]

# Statuses that satisfy the quality gate.
COVERED = {"consensus", "punct_rule", "corrected", "adjudicated",
           "record", "dropped", "watermark", "source_issue"}


def _verdicts_by_v4(base: Path) -> dict[tuple[str, int, int], Record]:
    verdicts: dict[tuple[str, int, int], Record] = {}
    for path in sorted((base / ".review" / "audit").glob("verdicts-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("v4_block") is None:
                continue
            verdicts[(row["notice"], row["image_index"], row["v4_block"])] = row
    return verdicts


def _verdicts_by_v6(base: Path) -> dict[tuple[str, int, int], Record]:
    verdicts: dict[tuple[str, int, int], Record] = {}
    for path in sorted((base / ".review" / "audit").glob("verdicts-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("v6_block") is None:
                continue
            verdicts[(row["notice"], row["image_index"], row["v6_block"])] = row
    return verdicts


def _ledger_maps(ledger: Record | None) -> tuple[dict, set, set, set]:
    """(corrected text map, drop set, human-correction set, rule-entry set)."""
    overrides = archive.corrected_blocks_raw(ledger or {})
    corrected = {loc: text for loc, text in overrides.items() if text is not None}
    drops = {loc for loc, text in overrides.items() if text is None}
    rules = set()
    for correction in (ledger or {}).get("corrections", []):
        if correction.get("insert"):
            continue
        loc = (correction["image_index"], correction["block_index"])
        if loc not in drops and correction.get("via") == "punct-rule":
            rules.add(loc)
    humans = set(corrected) - rules
    return corrected, drops, humans, rules


def _status_for_op(op: Record | None, verdict: Record | None) -> str:
    if verdict is not None:
        v = verdict.get("verdict")
        if v in ("v4_ok", "record", "v4_right"):
            return "record" if v in ("v4_ok", "record") else "adjudicated"
        if v == "source_issue":
            return "source_issue"
        if v in ("v6_right", "correct", "drop", "v6_garbage", "revert", "confirm",
                 "modify", "keep", "v4_covered"):
            return "adjudicated"
        return "queued"
    if op is None:
        return "queued"
    relation = op.get("relation")
    if relation in ("equal", "merged", "split", "punct"):
        return "consensus"
    return "queued"  # differ, v4_only, or unaligned


def _queued_v6_locators(base: Path) -> set[tuple[str, int, int]]:
    """v6 locators that appeared in an original adjudication queue.

    The queues were built from an earlier comparison; ledger drops since then
    can turn a formerly paired v6 block into a fresh v6_only. A locator that
    was queued once has an adjudicated verdict for its pair and must not be
    re-queued by the recomparison.
    """
    locators: set[tuple[str, int, int]] = set()
    for path in sorted((base / ".review" / "audit").glob("adj-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("v6_block") is not None:
                locators.add((row["notice"], row["image_index"], row["v6_block"]))
    return locators


def build_notice(
    feed: str, notice_v4: Record, ledger: Record | None, notice_v6: Record,
    verdicts_v4: dict, verdicts_v6: dict,
    queued_v6: set[tuple[str, int, int]] | None = None,
) -> Record:
    corrected, drops, humans, rules = _ledger_maps(ledger)
    compare = audit_compare.compare_notice(notice_v4, ledger, notice_v6)
    inserts = archive.insert_lines(ledger)
    ops_by_v4: dict[int, Record] = {}
    for image in compare.get("images", []):
        for op in image.get("ops", []):
            if op.get("v4") is not None:
                ops_by_v4[(image["index"], op["v4"])] = op
    ops_by_v6: dict[int, Record] = {}
    for image in compare.get("images", []):
        for op in image.get("ops", []):
            if op.get("v6") is not None and op.get("v4") is None:
                ops_by_v6[(image["index"], op["v6"])] = op

    blocks: list[Record] = []
    counts: dict[str, int] = {}
    for image in notice_v4.get("images", []):
        index = image["index"]
        for block_index, block in enumerate(image.get("blocks") or []):
            loc = (index, block_index)
            text = corrected.get(loc, block.get("text") or "")
            if loc in drops:
                status = "dropped"
            elif not edition.clean_block(text).strip():
                status = "watermark"
            elif loc in humans:
                status = "corrected"
            elif loc in rules:
                status = "punct_rule"
            else:
                op = ops_by_v4.get(loc)
                verdict = verdicts_v4.get((feed, index, block_index))
                status = _status_for_op(op, verdict)
            counts[status] = counts.get(status, 0) + 1
            blocks.append({
                "image_index": index,
                "block_index": block_index,
                "status": status,
                "text_sha256": hashlib.sha256(
                    (block.get("text") or "").encode("utf-8")).hexdigest()[:16],
            })

    # v6-only blocks: the v6 engine read a line the v4 record has no block for.
    # A block whose normalised text already appears in the image's effective
    # v4 text is content-covered (the v4 record lost nothing).
    effective_norm: dict[int, list[str]] = {}
    for image in notice_v4.get("images", []):
        index = image["index"]
        texts = []
        for block_index, block in enumerate(image.get("blocks") or []):
            loc = (index, block_index)
            if loc in drops:
                continue
            texts.append(audit_compare.normalize(
                corrected.get(loc, block.get("text") or "")))
        effective_norm[index] = texts
    v6_only: list[Record] = []
    for image in notice_v6.get("images", []):
        for block_index, block in enumerate(image.get("blocks") or []):
            op = ops_by_v6.get((image["index"], block_index))
            if op is None:
                continue  # matched to a v4 block; covered above
            if audit_compare.is_watermark(block.get("text") or ""):
                continue
            verdict = verdicts_v6.get((feed, image["index"], block_index))
            v = (verdict or {}).get("verdict")
            if v in ("v6_garbage", "drop"):
                status = "adjudicated"
            elif v in ("v6_right", "correct"):
                status = "adjudicated"  # real missed line; handled by the apply step
            elif v in ("v4_covered", "cover_art"):
                status = "adjudicated"
            elif queued_v6 and (feed, image["index"], block_index) in queued_v6:
                # Was paired in an original queue and adjudicated with it;
                # the recomparison stranded it after ledger drops.
                status = "adjudicated"
            else:
                norm = audit_compare.normalize(block.get("text") or "")
                covered = bool(norm) and len(norm) >= 4 and any(
                    norm in text or text in norm
                    for text in effective_norm.get(image["index"], [])
                    if text
                )
                status = "adjudicated" if covered else "queued"
            counts[status] = counts.get(status, 0) + 1
            v6_only.append({
                "image_index": image["index"],
                "v6_block": block_index,
                "text": block.get("text") or "",
                "status": status,
            })

    return {
        "id": f"dashen.{feed}",
        "engine_record": notice_v4.get("engine"),
        "engine_audit": notice_v6.get("engine"),
        "counts": counts,
        "blocks": blocks,
        "v6_only": v6_only,
        "inserts": [
            {"image_index": image_index, "after_block": after, "text": text,
             "status": "adjudicated"}
            for image_index, pairs in sorted(inserts.items())
            for after, text in pairs
        ],
    }


def run(root: str | Path | None = None, *, soft: bool = False) -> int:
    base = Path(root) if root else archive.REPO_ROOT
    out_dir = base / AUDIT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    verdicts_v4 = _verdicts_by_v4(base)
    verdicts_v6 = _verdicts_by_v6(base)
    queued_v6 = _queued_v6_locators(base)
    v6_dir = base / "derived" / "ocr-v6" / "dashen"
    totals: dict[str, int] = {}
    for feed, _ in archive.iter_ocr(base):
        v6_path = v6_dir / f"{feed}.json"
        if not v6_path.exists():
            print(f"audit-status: no v6 reading for {feed}", file=sys.stderr)
            continue
        result = build_notice(
            feed,
            archive.load_ocr(feed, base),
            archive.load_ledger(feed, base),
            json.loads(v6_path.read_text(encoding="utf-8")),
            verdicts_v4, verdicts_v6, queued_v6,
        )
        (out_dir / f"{feed}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        for key, value in result["counts"].items():
            totals[key] = totals.get(key, 0) + value
    queued = totals.get("queued", 0)
    print(f"audit-status: {json.dumps(totals, sort_keys=True)}")
    if queued:
        print(f"audit-status: {queued} queued blocks remain", file=sys.stderr)
    return 0 if soft or queued == 0 else 1
