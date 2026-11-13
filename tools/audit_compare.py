"""Cross-engine audit: align the v4 record against the v6 second reading.

The v4 layer (with the correction ledger applied) is the engine of record;
the v6 layer in ``derived/ocr-v6/dashen/`` is an independent second reading.
Per image, both block lists (ledger drops and watermark-only blocks removed)
are aligned as ordered text sequences with :mod:`difflib`, then a two-pointer
pass resolves one engine merging or splitting what the other read as separate
lines:

* ``equal``   - same characters, same punctuation (consensus);
* ``punct``   - same characters, differing punctuation/spacing (reviewed: the
  archive is faithful to the printed glyphs);
* ``differ``  - different characters (reviewed against the source image);
* ``merged`` / ``split`` - same text, different line segmentation (consensus);
* ``v4_only`` - the v6 engine found nothing where v4 read text;
* ``v6_only`` - the v6 engine read a line the v4 record has no block for;
* dropped ledger blocks and watermark-only blocks are outside the universe.

    python -m tools.cli audit-compare [--crops] [--notice ID]...

Outputs per-notice alignment JSON under ``.review/audit/compare/`` and the
review queue ``.review/audit/queue.jsonl`` (with evidence crops on
``--crops``).
"""

from __future__ import annotations

import difflib
import json
import unicodedata
from pathlib import Path
from typing import Any

from . import archive

COMPARE_DIR = Path(".review/audit/compare")
CROP_DIR = Path(".review/audit/crops")

Record = dict[str, Any]


class SelfAuditError(ValueError):
    """The record and its audit layer carry the same engine fingerprint.

    Comparing a reading with itself always yields consensus, so it would hide
    every engine error instead of measuring it. The second reader must be a
    different engine family.
    """


# How many consecutive blocks the merge/split rescue may consume.
MAX_GROUP = 4


def normalize(text: str) -> str:
    """CJK + alphanumerics only, full-width forms folded to ASCII."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(
        ch for ch in folded if "\u4e00" <= ch <= "\u9fff" or (ch.isascii() and ch.isalnum())
    )


def is_watermark(text: str) -> bool:
    """True when the block is only platform watermark (in any OCR spelling)."""
    from .edition import clean_block

    return not clean_block(text).strip()


def _walk_replace(
    v4_span: list[tuple[int, str]], v6_span: list[tuple[int, str]]
) -> list[Record]:
    """Resolve a mismatched span: merges, splits, then real differences."""
    ops: list[Record] = []
    i = j = 0
    while i < len(v4_span) and j < len(v6_span):
        loc4, text4 = v4_span[i]
        loc6, text6 = v6_span[j]
        if normalize(text4) == normalize(text6):
            ops.append({"v4": loc4, "v6": loc6, "relation": "equal"})
            i += 1
            j += 1
            continue
        for k in range(2, MAX_GROUP + 1):  # v6 merged k consecutive v4 lines
            if i + k <= len(v4_span) and normalize(
                "".join(text for _, text in v4_span[i : i + k])
            ) == normalize(text6):
                for offset in range(k):
                    ops.append({"v4": v4_span[i + offset][0], "v6": loc6, "relation": "merged"})
                i += k
                j += 1
                break
        else:
            for k in range(2, MAX_GROUP + 1):  # v6 split one v4 line into k blocks
                if j + k <= len(v6_span) and normalize(text4) == normalize(
                    "".join(text for _, text in v6_span[j : j + k])
                ):
                    for offset in range(k):
                        ops.append(
                            {"v4": loc4, "v6": v6_span[j + offset][0], "relation": "split"}
                        )
                    i += 1
                    j += k
                    break
            else:
                ops.append(
                    {
                        "v4": loc4,
                        "v6": loc6,
                        "relation": "differ",
                        "v4_text": text4,
                        "v6_text": text6,
                    }
                )
                i += 1
                j += 1
    for loc4, text4 in v4_span[i:]:
        ops.append({"v4": loc4, "v6": None, "relation": "v4_only", "v4_text": text4})
    for loc6, text6 in v6_span[j:]:
        ops.append({"v4": None, "v6": loc6, "relation": "v6_only", "v6_text": text6})
    return ops


def _residual_match(ops: list[Record]) -> None:
    """Pair leftover v4-only/v6-only blocks whose texts are identical.

    difflib anchors on the longest runs, so a locally scrambled reading can
    strand identical blocks on both sides; matching them by content recovers
    those as consensus instead of queueing phantom differences.
    """
    by_norm: dict[str, list[Record]] = {}
    for op in ops:
        if op["relation"] == "v6_only":
            by_norm.setdefault(normalize(op.get("v6_text") or ""), []).append(op)
    matched: set[int] = set()
    out: list[Record] = []
    for op in ops:
        if op["relation"] == "v4_only":
            candidates = by_norm.get(normalize(op.get("v4_text") or ""))
            if candidates:
                match = candidates.pop(0)
                matched.add(id(match))
                out.append({"v4": op["v4"], "v6": match["v6"], "relation": "equal"})
                continue
        out.append(op)
    ops[:] = [op for op in out if id(op) not in matched]


def align_texts(
    v4_items: list[tuple[int, str]], v6_items: list[tuple[int, str]]
) -> list[Record]:
    """Align two ordered block readings into classified ops.

    ``equal`` ops carry no relation text yet: the caller decides ``equal`` vs
    ``punct`` from the raw strings.
    """
    a = [normalize(text) for _, text in v4_items]
    b = [normalize(text) for _, text in v6_items]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops: list[Record] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ops.append({"v4": v4_items[i1 + k][0], "v6": v6_items[j1 + k][0], "relation": "equal"})
        elif tag == "replace":
            ops.extend(_walk_replace(v4_items[i1:i2], v6_items[j1:j2]))
        elif tag == "delete":
            ops.extend(
                {"v4": loc, "v6": None, "relation": "v4_only", "v4_text": text}
                for loc, text in v4_items[i1:i2]
            )
        elif tag == "insert":
            ops.extend(
                {"v4": None, "v6": loc, "relation": "v6_only", "v6_text": text}
                for loc, text in v6_items[j1:j2]
            )
    _residual_match(ops)
    return ops


def _finish_equal_ops(ops: list[Record], raw_v4: dict[int, str], raw_v6: dict[int, str]) -> None:
    """Split ``equal`` ops into ``equal`` and ``punct`` using the raw strings."""
    for op in ops:
        if op["relation"] != "equal":
            continue
        text4, text6 = raw_v4[op["v4"]], raw_v6[op["v6"]]
        if text4 == text6:
            continue
        op["relation"] = "punct"
        op["v4_text"], op["v6_text"] = text4, text6


def compare_notice(notice_v4: Record, ledger: Record | None, notice_v6: Record) -> Record:
    """Align one notice's two readings and classify every block.

    Raises :class:`SelfAuditError` when both records carry the same engine
    fingerprint: an engine cannot audit itself.
    """
    engine_record = notice_v4.get("engine")
    engine_audit = notice_v6.get("engine")
    if engine_record and engine_record == engine_audit:
        raise SelfAuditError(
            f"refusing to audit {notice_v4.get('id')}: record and audit both "
            f"carry engine {engine_record}; run the second reader with a "
            f"different engine"
        )

    overrides = archive.corrected_blocks_raw(ledger or {})
    corrected = {loc: text for loc, text in overrides.items() if text is not None}
    dropped = {loc for loc, text in overrides.items() if text is None}

    result: Record = {
        "id": notice_v4["id"],
        "engine_record": engine_record,
        "engine_audit": engine_audit,
        "images": [],
    }
    counts = {
        "equal": 0, "punct": 0, "differ": 0, "merged": 0, "split": 0,
        "v4_only": 0, "v6_only": 0, "watermark": 0, "dropped": 0,
    }
    v6_images = {image["index"]: image for image in notice_v6.get("images", [])}
    for image in notice_v4.get("images", []):
        index = image["index"]
        entry: Record = {"index": index, "ops": []}
        v6_image = v6_images.get(index)
        if v6_image is None:
            entry["error"] = "no v6 reading for this image"
            result["images"].append(entry)
            continue
        v4_blocks = image.get("blocks") or []
        v6_blocks = v6_image.get("blocks") or []

        v4_items: list[tuple[int, str]] = []
        raw_v4: dict[int, str] = {}
        for i, block in enumerate(v4_blocks):
            text = corrected.get((index, i), block.get("text") or "")
            if (index, i) in dropped:
                counts["dropped"] += 1
                continue
            if is_watermark(text):
                counts["watermark"] += 1
                continue
            raw_v4[i] = text
            v4_items.append((i, text))
        v6_items: list[tuple[int, str]] = []
        raw_v6: dict[int, str] = {}
        for j, block in enumerate(v6_blocks):
            text = block.get("text") or ""
            if is_watermark(text):
                counts["watermark"] += 1
                continue
            raw_v6[j] = text
            v6_items.append((j, text))

        ops = align_texts(v4_items, v6_items)
        _finish_equal_ops(ops, raw_v4, raw_v6)
        for op in ops:
            counts[op["relation"]] += 1
        entry["ops"] = ops
        result["images"].append(entry)
    for image in notice_v6.get("images", []):
        if not any(entry["index"] == image["index"] for entry in result["images"]):
            result["images"].append({"index": image["index"], "error": "no v4 image"})
    result["counts"] = counts
    return result


def _render_crop(
    notice_v4: Record, image_index: int, block_index: int, out: Path
) -> str | None:
    """Evidence crop around one v4 block; None when the block has no box."""
    from . import crop as crop_mod

    images = notice_v4.get("images", [])
    if not 1 <= image_index <= len(images):
        return None
    image = images[image_index - 1]
    blocks = image.get("blocks") or []
    if not 0 <= block_index < len(blocks):
        return None
    box = crop_mod.crop_box(blocks, block_index, context=1, pad_x=30, pad_y=8)
    rendered = crop_mod.render(archive.REPO_ROOT / image["path"], box, target_width=1400)
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(out, format="PNG")
    return out.as_posix()


def _render_v6_crop(
    notice_v6: Record, image_index: int, block_index: int, out: Path
) -> str | None:
    """Evidence crop around a block that only the v6 engine found.

    The v4 record has no geometry for it, so the crop is cut from the v6 box.
    """
    from . import crop as crop_mod

    images = notice_v6.get("images", [])
    if not 1 <= image_index <= len(images):
        return None
    image = images[image_index - 1]
    blocks = image.get("blocks") or []
    if not 0 <= block_index < len(blocks):
        return None
    box = crop_mod.crop_box(blocks, block_index, context=1, pad_x=30, pad_y=8)
    rendered = crop_mod.render(archive.REPO_ROOT / image["path"], box, target_width=1400)
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(out, format="PNG")
    return out.as_posix()


def build_queue(
    comparisons: list[Record], root: Path, render_crops: bool = False
) -> list[Record]:
    """Flatten comparisons into review items for the four queue-worthy kinds."""
    queue: list[Record] = []
    for result in comparisons:
        feed = result["id"].split(".", 1)[1]
        notice_v4 = archive.load_ocr(feed, root)
        notice_v6 = json.loads(
            (root / "derived" / "ocr-v6" / "dashen" / f"{feed}.json").read_text(encoding="utf-8")
        )
        for image in result["images"]:
            index = image["index"]
            for op in image.get("ops", []):
                if op["relation"] not in ("differ", "punct", "v4_only", "v6_only"):
                    continue
                item = {
                    "notice": feed,
                    "image_index": index,
                    "v4_block": op["v4"],
                    "v6_block": op["v6"],
                    "kind": op["relation"],
                    "v4_text": op.get("v4_text"),
                    "v6_text": op.get("v6_text"),
                    "crop": None,
                }
                if render_crops and op["v4"] is not None:
                    item["crop"] = _render_crop(
                        notice_v4, index, op["v4"],
                        root / CROP_DIR / feed / f"img{index:02d}-v4b{op['v4']:04d}.png",
                    )
                elif render_crops and op["v6"] is not None:
                    item["crop"] = _render_v6_crop(
                        notice_v6, index, op["v6"],
                        root / CROP_DIR / feed / f"img{index:02d}-v6b{op['v6']:04d}.png",
                    )
                queue.append(item)
    return queue


def run_compare(
    root: str | Path | None = None,
    *,
    notice_ids: list[str] | None = None,
    crops: bool = False,
) -> dict[str, int]:
    """Compare every v4 notice against its v6 reading; write compare JSON + queue."""
    base = Path(root) if root else archive.REPO_ROOT
    v6_dir = base / "derived" / "ocr-v6" / "dashen"
    compare_dir = base / COMPARE_DIR
    compare_dir.mkdir(parents=True, exist_ok=True)

    feeds = notice_ids or [path.stem for path in sorted(v6_dir.glob("*.json"))]
    stats = {
        "notices": 0, "skipped": 0, "equal": 0, "punct": 0, "differ": 0,
        "merged": 0, "split": 0, "v4_only": 0, "v6_only": 0,
        "watermark": 0, "dropped": 0,
    }
    comparisons: list[Record] = []
    for feed in feeds:
        v6_path = v6_dir / f"{feed}.json"
        if not v6_path.exists():
            stats["skipped"] += 1
            continue
        notice_v4 = archive.load_ocr(feed, base)
        notice_v6 = json.loads(v6_path.read_text(encoding="utf-8"))
        result = compare_notice(notice_v4, archive.load_ledger(feed, base), notice_v6)
        (compare_dir / f"{feed}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
        comparisons.append(result)
        for key, value in result["counts"].items():
            stats[key] = stats.get(key, 0) + value
        stats["notices"] += 1
    queue = build_queue(comparisons, base, crops)
    queue_path = base / ".review" / "audit" / "queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "w", encoding="utf-8", newline="\n") as fh:
        for item in queue:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    stats["queue"] = len(queue)
    return stats
