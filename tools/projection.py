"""The shared projection from an OCR record plus its correction ledger.

The Markdown edition and the website dataset both call
:func:`effective_sections`, so the ledger, watermark rule, insert anchors and
duplicate-line suppression are applied once. The OCR block list itself is never
rewritten: corrections and inserts address ``block_index``, so reading order is
a display-only reordering.
"""

from __future__ import annotations

from typing import Any

from . import archive, text

Record = dict[str, Any]

# Two blocks share a visual line when their boxes overlap vertically by more
# than half of the shorter block. The centre guard stops a tall multi-line
# block from absorbing its neighbours; blocks taller than the line-height
# limit act as barriers and keep their original position.
MIN_VERTICAL_OVERLAP = 0.5
MAX_CENTRE_DISTANCE = 0.6
MAX_LINE_HEIGHT_FACTOR = 1.6
MIN_X_INVERSION = 20.0

# Canonical priority when several sources cover the same date.
SOURCE_PRIORITY = {"nga": 0, "dashen": 1}


def _bbox(block: Record) -> tuple[float, float, float, float] | None:
    box = block.get("box") or []
    if not box:
        return None
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, ValueError, IndexError):
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _same_line(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
    max_height: float,
) -> bool:
    if first is None or second is None:
        return False
    _, top_a, _, bottom_a = first
    _, top_b, _, bottom_b = second
    height_a = bottom_a - top_a
    height_b = bottom_b - top_b
    if height_a <= 0 or height_b <= 0:
        return False
    if height_a > max_height or height_b > max_height:
        return False
    overlap = max(0.0, min(bottom_a, bottom_b) - max(top_a, top_b))
    if overlap / min(height_a, height_b) < MIN_VERTICAL_OVERLAP:
        return False
    centre_a = (top_a + bottom_a) / 2
    centre_b = (top_b + bottom_b) / 2
    return abs(centre_a - centre_b) <= MAX_CENTRE_DISTANCE * max(height_a, height_b)


def _left_top(item: Record) -> tuple[float, float]:
    box = _bbox(item["block"])
    return (box[0], box[1]) if box else (0.0, 0.0)


def _reading_order(items: list[Record]) -> list[Record]:
    """Flatten items into display order, left-to-right within a visual line.

    A line is only re-sorted when its original order actually contains a
    right-to-left inversion; already ordered lines keep their OCR order, so
    tables and decorative layouts are not touched.
    """
    if len(items) < 2:
        return items
    boxes = [_bbox(item["block"]) for item in items]
    heights = [box[3] - box[1] for box in boxes if box is not None and box[3] > box[1]]
    max_height = MAX_LINE_HEIGHT_FACTOR * _median(heights) if heights else 0.0

    groups: list[list[int]] = []
    for index, box in enumerate(boxes):
        if groups and _same_line(boxes[groups[-1][-1]], box, max_height):
            groups[-1].append(index)
        else:
            groups.append([index])

    ordered: list[Record] = []
    for group in groups:
        current = [items[index] for index in group]
        lefts = [boxes[index][0] for index in group if boxes[index] is not None]
        inverted = len(lefts) == len(group) and any(
            lefts[position] > lefts[position + 1] + MIN_X_INVERSION
            for position in range(len(lefts) - 1)
        )
        if inverted:
            current.sort(key=_left_top)
        ordered.extend(current)
    return ordered


def effective_sections(
    notice: Record | None, ledger: Record | None
) -> list[tuple[int, list[str]]]:
    """Readable lines per image, with the correction ledger applied.

    Returns ``(image_index, lines)`` pairs. Lines are in visual reading order;
    dropped blocks and watermark-only blocks contribute no line, while an
    insert anchored after a block stays with that block when its line moves.
    """
    if not notice:
        return []
    overrides = archive.corrected_blocks(notice, ledger)
    inserts = archive.insert_lines(ledger)
    sections: list[tuple[int, list[str]]] = []

    for image in notice.get("images", []):
        index = image["index"]
        blocks = image.get("blocks") or []
        items: list[Record] = []
        for block_index, block in enumerate(blocks):
            locator = (index, block_index)
            if locator in overrides:
                replacement = overrides[locator]
                value = None if replacement is None else replacement
            else:
                value = block.get("text") or ""
            items.append(
                {"block_index": block_index, "block": block, "text": value, "inserts": []}
            )
        for after, extra in inserts.get(index, []):
            if 0 <= after < len(items):
                items[after]["inserts"].append(extra)

        lines: list[str] = []
        previous = ""
        for item in _reading_order(items):
            value = item["text"]
            if value is not None:
                cleaned = text.clean_block(value)
                if cleaned and cleaned != previous:
                    lines.append(cleaned)
                    previous = cleaned
            for extra in item["inserts"]:
                cleaned_extra = text.clean_block(extra)
                if cleaned_extra and cleaned_extra != previous:
                    lines.append(cleaned_extra)
                    previous = cleaned_extra
        if lines:
            sections.append((index, lines))
    return sections


def assign_canonical(rows: list[Record]) -> None:
    """Mark exactly one canonical reading per ``(family, notice_date)``.

    The preferred source is NGA (the operator's plain text); otherwise the
    earliest Dashen row wins. A row without a ``notice_date`` is never
    canonical: the archive has no basis for choosing between undated notices.
    """
    groups: dict[tuple[Any, Any], list[Record]] = {}
    for row in rows:
        groups.setdefault((row.get("family", "update"), row.get("notice_date")), []).append(row)

    for (_family, notice_date), group in groups.items():
        for row in group:
            row["canonical"] = False
        if not notice_date:
            continue
        winner = min(
            group,
            key=lambda row: (
                SOURCE_PRIORITY.get(row.get("source"), len(SOURCE_PRIORITY)),
                row.get("published_at") or "",
                row.get("id") or "",
            ),
        )
        winner["canonical"] = True
