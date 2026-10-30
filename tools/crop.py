"""Geometry and rendering for source-image evidence crops.

A crop shows one OCR block together with its line neighbours, so a reviewer can
compare the engine's reading with the printed glyphs. Geometry is pure and
testable without Pillow; only :func:`render` imports the imaging library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

Box = tuple[int, int, int, int]
Block = dict[str, Any]

LINE_OVERLAP = 0.5
CONTEXT_GAP = 80
MAX_SCALE = 4.0


def block_box(block: Block) -> Box:
    xs = [int(point[0]) for point in block["box"]]
    ys = [int(point[1]) for point in block["box"]]
    return min(xs), min(ys), max(xs), max(ys)


def same_line(block: Block, other: Block) -> bool:
    _, top, _, bottom = block_box(block)
    _, other_top, _, other_bottom = block_box(other)
    overlap = min(bottom, other_bottom) - max(top, other_top)
    height = min(bottom - top, other_bottom - other_top)
    return height > 0 and overlap / height >= LINE_OVERLAP


def crop_box(
    blocks: Sequence[Block],
    block_index: int,
    *,
    context: int = 0,
    pad_x: int = 40,
    pad_y: int = 12,
) -> Box:
    """Box around one block: whole line, padding, and ``context`` lines around it."""
    if not 0 <= block_index < len(blocks):
        raise IndexError(f"block index out of range: {block_index}")

    target = blocks[block_index]
    left, top, right, bottom = block_box(target)
    for block in blocks:
        if same_line(block, target):
            block_left, _, block_right, _ = block_box(block)
            left, right = min(left, block_left), max(right, block_right)

    left = max(0, left - pad_x)
    right += pad_x
    top = max(0, top - pad_y)
    bottom += pad_y

    if context:
        ordered = sorted(
            range(len(blocks)),
            key=lambda index: (block_box(blocks[index])[1], block_box(blocks[index])[0]),
        )
        position = ordered.index(block_index)
        first = last = position
        for offset in range(1, context + 1):
            above = position - offset
            if above < 0 or top - block_box(blocks[ordered[above]])[3] > CONTEXT_GAP:
                break
            first = above
        for offset in range(1, context + 1):
            below = position + offset
            if below >= len(ordered) or block_box(blocks[ordered[below]])[1] - bottom > CONTEXT_GAP:
                break
            last = below
        top = max(0, min(top, block_box(blocks[ordered[first]])[1] - pad_y))
        bottom = max(bottom, block_box(blocks[ordered[last]])[3] + pad_y)

    return left, top, right, bottom


def render(
    image_path: Path,
    box: Box,
    *,
    scale: float = 0.0,
    target_width: float = 1400.0,
):
    """Upscaled crop of ``image_path``. ``scale=0`` fits ``target_width``."""
    from PIL import Image

    with Image.open(image_path) as source:
        image = source.convert("RGB")

    left, top, right, bottom = box
    right = min(right, image.width)
    bottom = min(bottom, image.height)
    left = min(left, max(0, right - 1))
    top = min(top, max(0, bottom - 1))
    crop = image.crop((left, top, right, bottom))
    if scale <= 0:
        scale = max(1.0, min(MAX_SCALE, target_width / max(crop.width, 1)))
    if scale != 1.0:
        crop = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
            Image.LANCZOS,
        )
    return crop
