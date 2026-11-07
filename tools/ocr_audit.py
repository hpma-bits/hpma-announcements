"""Second-reader OCR: run rapidocr 3.x (PP-OCRv6 small) over notice images.

The v4 layer in ``derived/ocr/dashen/`` stays the engine of record; this writes
a parallel v6 reading to ``derived/ocr-v6/dashen/`` so the cross-engine audit
can compare two independent readings of the same image. Nothing here touches
the network: every image must already exist under ``assets/dashen/``.

    python -m tools.cli audit-ocr [--family update] [--limit N]

Idempotent: per-image results are cached by sha256 under the engine
fingerprint, so an interrupted run resumes without re-OCR.
"""

from __future__ import annotations

from pathlib import Path

from collectors import ocr

from . import archive

V6_DIR = "derived/ocr-v6/dashen"

Engine = ocr.Engine
Block = ocr.Block


def audit_engine() -> tuple[Engine, dict[str, str]]:
    """Build the rapidocr 3.x engine. Heavy dependency; imported lazily.

    Same windowed reading as the v4 engine: tall images are sliced into
    overlapping 1280 px windows and per-window lines are merged, so the two
    layers differ only in what the models read, not in how images are cut.
    """
    from importlib.metadata import version

    import cv2
    from rapidocr import RapidOCR

    rapid = RapidOCR()

    def raw_to_lines(result, y_offset: int) -> list[tuple[float, str, float, list[list[int]]]]:
        boxes = result.boxes if result.boxes is not None else []
        txts = result.txts if result.txts is not None else []
        scores = result.scores if result.scores is not None else []
        out = []
        for box, text, score in zip(boxes, txts, scores):
            pts = [[int(x), int(y) + y_offset] for x, y in box]
            cy = sum(y for _, y in pts) / len(pts)
            out.append((cy, str(text), float(score or 0.0), pts))
        return out

    def engine(path: str) -> list[Block]:
        image = cv2.imread(path)
        if image is None:
            raise ValueError(f"cannot read image {path}")
        height, _ = image.shape[:2]
        lines: list[tuple[float, str, float, list[list[int]]]] = []
        for start, end in ocr.tiles(height):
            result = rapid(image[start:end, :], use_cls=False)
            lines.extend(raw_to_lines(result, start))
        return ocr.merge_lines(lines)

    meta = {
        "name": "rapidocr",
        "version": version("rapidocr"),
        "model": "PP-OCRv6-small",
        "tiling": "1280+256",
        "cls": "off",
    }
    return engine, meta


def run(
    root: str | Path | None = None,
    *,
    family: str = "update",
    limit: int | None = None,
) -> dict[str, int]:
    """OCR every notice image with the v6 engine into the audit layer."""
    root_path = Path(root) if root else archive.REPO_ROOT
    engine, engine_meta = audit_engine()
    return ocr.run(
        root_path,
        family=family,
        limit=limit,
        engine=engine,
        engine_meta=engine_meta,
        out_dir=V6_DIR,
        download=False,
        delay=0,
    )
