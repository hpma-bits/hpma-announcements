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

The evidence is tracked primary data:

    audit/verdicts-*.jsonl                  adjudicated verdicts
    audit/adj-*.jsonl                       the queues those verdicts answer

``derived/audit/dashen/<feed_id>.json`` is the projection rebuilt from it.
Crops stay in the gitignored ``.review/`` workspace and are never read here.

    python -m tools.cli audit-status [--check | --soft]

Before any ledger is rebuilt, both modes enforce coverage over the notices the
archive is expected to hold (``archive.selected_posts(base, "update",
"dashen")``): every expected notice needs a v4 OCR record and a v6 reading,
every v4 record needs a v6 reading, no image may carry an OCR error, and no
layer may contain zero blocks. A missing notice or an all-failed reading is a
hard failure, not a silently empty ledger; this is what keeps ``totals={}``
from masquerading as a clean run.

Write mode rebuilds every ledger and exits 1 while ``queued`` blocks remain,
unless ``--soft``. ``--check`` rebuilds in memory, compares each committed
ledger byte for byte, writes nothing, and exits 1 on any difference or queued
block. Either mode refuses to run when the tracked evidence is missing or the
coverage gate fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import archive, audit_compare, edition

EVIDENCE_DIR = Path("audit")
AUDIT_DIR = Path("derived/audit/dashen")
V6_DIR = Path("derived/ocr-v6/dashen")

Record = dict[str, Any]


class MissingEvidenceError(RuntimeError):
    """The tracked audit evidence is absent, so nothing can be rebuilt."""


class CoverageError(RuntimeError):
    """Expected notices lack a complete, independent OCR layer."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _evidence_paths(base: Path) -> tuple[list[Path], list[Path]]:
    """The tracked verdict and queue files; refuse to run without both."""
    evidence = base / EVIDENCE_DIR
    verdicts = sorted(evidence.glob("verdicts-*.jsonl"))
    adj = sorted(evidence.glob("adj-*.jsonl"))
    missing = [
        pattern
        for pattern, paths in (("verdicts-*.jsonl", verdicts), ("adj-*.jsonl", adj))
        if not paths
    ]
    if missing:
        raise MissingEvidenceError(
            f"audit evidence missing: {EVIDENCE_DIR.as_posix()}/ has no "
            f"{' or '.join(missing)} (tracked primary data; see audit/README.md)"
        )
    return verdicts, adj


def _verdicts_by_v4(paths: list[Path]) -> dict[tuple[str, int, int], Record]:
    verdicts: dict[tuple[str, int, int], Record] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("v4_block") is None:
                continue
            verdicts[(row["notice"], row["image_index"], row["v4_block"])] = row
    return verdicts


def _verdicts_by_v6(paths: list[Path]) -> dict[tuple[str, int, int], Record]:
    verdicts: dict[tuple[str, int, int], Record] = {}
    for path in paths:
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


def _queued_v6_locators(paths: list[Path]) -> set[tuple[str, int, int]]:
    """v6 locators that appeared in an original adjudication queue.

    The queues were built from an earlier comparison; ledger drops since then
    can turn a formerly paired v6 block into a fresh v6_only. A locator that
    was queued once has an adjudicated verdict for its pair and must not be
    re-queued by the recomparison.
    """
    locators: set[tuple[str, int, int]] = set()
    for path in paths:
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


def _serialize(result: Record) -> bytes:
    return (
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _expected_feeds(base: Path) -> list[str]:
    """Feed ids the archive is supposed to hold, from the tracked corpus.

    A tmp fixture without corpus/selection has no expectations; the real
    archive always has both, so a missing layer fails the coverage gate.
    """
    corpus = archive.corpus_path(base, "dashen")
    selection = archive.selection_path(base, "dashen")
    if not corpus.is_file() or not selection.is_file():
        return []
    return [post["id"] for post, _ in archive.selected_posts(base, "update", "dashen")]


def _layer_problems(layer: str, feed: str, notice: Record) -> list[str]:
    """OCR errors and wholly empty readings in one layer of one notice."""
    problems: list[str] = []
    images = notice.get("images") or []
    for image in images:
        if image.get("error"):
            problems.append(
                f"{layer} image {feed}/{image.get('index')} failed: {image['error']}"
            )
    blocks = sum(len(image.get("blocks") or []) for image in images)
    if not images:
        problems.append(f"{layer} OCR for {feed} has no images")
    elif blocks == 0:
        problems.append(f"{layer} OCR for {feed} has zero text blocks")
    return problems


def _coverage_problems(
    base: Path,
    notices_v4: dict[str, Record],
    notices_v6: dict[str, Record],
) -> list[str]:
    """Every expected-notice or layer-completeness violation."""
    problems: list[str] = []
    for feed in _expected_feeds(base):
        if feed not in notices_v4:
            problems.append(f"missing v4 OCR for expected notice {feed}")
    for feed in sorted(notices_v4):
        if feed not in notices_v6:
            problems.append(f"missing v6 reading for {feed}")
        problems.extend(_layer_problems("v4", feed, notices_v4[feed]))
    for feed in sorted(notices_v6):
        problems.extend(_layer_problems("v6", feed, notices_v6[feed]))
    return list(dict.fromkeys(problems))


def build(base: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    """Rebuild every ledger in memory; ``(feed -> bytes, totals)``.

    Reads only tracked primary data (``audit/``, ``derived/ocr/``,
    ``derived/ocr-v6/`` and ``corrections/``). Raises
    :class:`MissingEvidenceError` before any caller can write when either
    evidence group is absent, and :class:`CoverageError` when an expected
    notice or one of its two readings is missing, errored, or empty.
    """
    verdict_paths, adj_paths = _evidence_paths(base)
    verdicts_v4 = _verdicts_by_v4(verdict_paths)
    verdicts_v6 = _verdicts_by_v6(verdict_paths)
    queued_v6 = _queued_v6_locators(adj_paths)
    v6_dir = base / V6_DIR
    notices_v4 = dict(archive.iter_ocr(base))
    notices_v6: dict[str, Record] = {}
    for feed in notices_v4:
        v6_path = v6_dir / f"{feed}.json"
        if v6_path.is_file():
            notices_v6[feed] = json.loads(v6_path.read_text(encoding="utf-8"))

    problems = _coverage_problems(base, notices_v4, notices_v6)
    if problems:
        raise CoverageError(problems)

    payloads: dict[str, bytes] = {}
    totals: dict[str, int] = {}
    for feed, notice_v4 in notices_v4.items():
        result = build_notice(
            feed,
            notice_v4,
            archive.load_ledger(feed, base),
            notices_v6[feed],
            verdicts_v4, verdicts_v6, queued_v6,
        )
        payloads[feed] = _serialize(result)
        for key, value in result["counts"].items():
            totals[key] = totals.get(key, 0) + value
    return payloads, totals


def _check(payloads: dict[str, bytes], out_dir: Path, base: Path) -> list[str]:
    """Byte-for-byte comparison against the committed projection."""
    if not out_dir.is_dir():
        return [f"missing {AUDIT_DIR.as_posix()}/"]
    problems: list[str] = []
    for feed, payload in sorted(payloads.items()):
        path = out_dir / f"{feed}.json"
        if not path.is_file():
            problems.append(f"missing {path.relative_to(base).as_posix()}")
        elif path.read_bytes() != payload:
            problems.append(f"stale {path.relative_to(base).as_posix()}")
    expected = {f"{feed}.json" for feed in payloads}
    for path in sorted(out_dir.glob("*.json")):
        if path.name not in expected:
            problems.append(f"orphan {path.relative_to(base).as_posix()}")
    return problems


def _atomic_write(path: Path, payload: bytes) -> None:
    """Replace ``path`` in one step; never expose a half-written ledger."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(root: str | Path | None = None, *, soft: bool = False,
        check: bool = False) -> int:
    if check and soft:
        raise ValueError("--check and --soft are mutually exclusive")
    base = Path(root) if root else archive.REPO_ROOT
    out_dir = base / AUDIT_DIR
    # Build first: missing evidence or an incomplete corpus must fail before
    # the output is touched, in both check and write mode.
    try:
        payloads, totals = build(base)
    except CoverageError as exc:
        for problem in exc.problems:
            print(f"audit-status: {problem}", file=sys.stderr)
        return 1
    queued = totals.get("queued", 0)

    if check:
        problems = _check(payloads, out_dir, base)
        for problem in problems:
            print(f"audit-status: {problem}", file=sys.stderr)
        print(f"audit-status: {json.dumps(totals, sort_keys=True)}")
        if queued:
            print(f"audit-status: {queued} queued blocks remain", file=sys.stderr)
        if problems or queued:
            return 1
        print(f"audit-status: {len(payloads)} ledgers up to date")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    for feed, payload in sorted(payloads.items()):
        path = out_dir / f"{feed}.json"
        if path.is_file() and path.read_bytes() == payload:
            continue
        _atomic_write(path, payload)
        changed += 1
    print(f"audit-status: wrote {len(payloads)} ledgers ({changed} changed) "
          f"to {AUDIT_DIR.as_posix()}")
    print(f"audit-status: {json.dumps(totals, sort_keys=True)}")
    if queued:
        print(f"audit-status: {queued} queued blocks remain", file=sys.stderr)
    return 0 if soft or queued == 0 else 1
