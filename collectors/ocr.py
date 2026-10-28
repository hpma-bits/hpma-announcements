"""OCR pipeline for long-image update notices.

For every post in a label family (``study/labels.jsonl``), downloads its
images as original bytes under ``assets/dashen/<feed_id>/``, runs a local OCR
engine, and writes one JSON per notice under ``derived/ocr/dashen/<feed_id>.json``.
Corpus text is never modified; OCR output is a derived layer.

Engine: rapidocr-onnxruntime (PP-OCRv4 models), installed separately via
``requirements-ocr.txt`` so the base test suite stays dependency-light. The
import is lazy and the engine is injectable for tests.

Reruns are idempotent: existing image bytes are not re-downloaded, and an
image whose ``sha256`` already has text in the notice JSON is not re-OCRed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import http
from .store import REPO_ROOT, sha256_bytes

Fetcher = Callable[[str], bytes]
Block = dict[str, Any]
Engine = Callable[[str], list[Block]]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def sniff_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(PNG_MAGIC):
        return ".png"
    return ".bin"


def tiles(height: int, window: int = 1280, step: int = 1024) -> list[tuple[int, int]]:
    """Vertical (start, end) slices covering ``height`` with overlap.

    Long notices are 6000-10000 px tall; detection models downscale whole
    images to uselessness, so tall images are OCR'd window by window and the
    overlapping lines are merged.
    """
    if height <= window:
        return [(0, height)]
    spans = [(start, min(start + window, height)) for start in range(0, height, step)]
    if spans[-1][1] - spans[-1][0] < 64 and len(spans) > 1:
        spans[-2] = (spans[-2][0], height)
        spans.pop()
    return spans


def merge_lines(
    lines: list[tuple[float, str, float, list[list[int]]]],
    tolerance: float = 40.0,
) -> list[Block]:
    """Merge OCR lines from overlapping windows.

    ``lines`` are (center_y_absolute, text, score, box). Lines with the same
    text whose centers are within ``tolerance`` px are duplicates from the
    overlap zone; the first (highest) occurrence wins. Output is sorted
    top-to-bottom.
    """
    kept: list[tuple[float, str, float, list[list[int]]]] = []
    for cy, text, score, box in sorted(lines, key=lambda item: (item[0], item[1])):
        if any(text == other[1] and abs(cy - other[0]) < tolerance for other in kept):
            continue
        kept.append((cy, text, score, box))
    kept.sort(key=lambda item: item[0])
    return [{"box": box, "text": text, "score": round(score, 4)} for cy, text, score, box in kept]


def default_engine() -> tuple[Engine, dict[str, str]]:
    """Build the real OCR engine. Heavy dependency; imported lazily.

    Tall images are read with OpenCV, sliced into overlapping windows, and
    OCR'd window by window; per-window results are re-offset to absolute
    coordinates and merged.
    """
    from importlib.metadata import version

    import cv2
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()

    def raw_to_lines(result, y_offset: int, width: int) -> list[tuple[float, str, float, list[list[int]]]]:
        out = []
        for box, text, score in (result or []):
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
        for start, end in tiles(height):
            # use_cls=False: notice images are upright, and the angle
            # classifier dominates runtime on CPU (30s+ per image).
            result, _ = ocr(image[start:end, :], use_cls=False)
            lines.extend(raw_to_lines(result, start, image.shape[1]))
        return merge_lines(lines)

    meta = {
        "name": "rapidocr-onnxruntime",
        "version": version("rapidocr-onnxruntime"),
        "model": "PP-OCRv4",
        "tiling": "1280+256",
        "cls": "off",
    }
    return engine, meta


def load_posts(root: Path, family: str) -> list[dict[str, Any]]:
    corpus_path = root / "corpus" / "dashen" / "posts.jsonl"
    labels_path = root / "study" / "labels.jsonl"
    posts = [
        json.loads(line)
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = {
        (row := json.loads(line))["id"]: row["label"]
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    return [post for post in posts if labels.get(post["id"]) == family]


INLINE_IMG = re.compile(r'<img[^>]+src="(https?://[^"]+)"')


def image_urls_for(post: dict[str, Any]) -> list[str]:
    """All images belonging to a post: feed media plus inline images embedded
    in a type-6 article's longText (their covers alone carry no content)."""
    urls = list(post.get("image_urls") or [])
    for src in INLINE_IMG.findall(post.get("long_text") or ""):
        if src not in urls:
            urls.append(src)
    return urls


def _local_image(asset_dir: Path, index: int) -> Path | None:
    matches = sorted(asset_dir.glob(f"{index:02d}.*"))
    return matches[0] if matches else None


def _image_bytes(
    asset_dir: Path,
    index: int,
    url: str,
    fetch: Fetcher,
    stats: dict[str, int],
) -> tuple[bytes, int]:
    """Return image bytes, downloading only when no local copy exists.

    The int is 1 when a download happened (callers pace with a delay).
    """
    local = _local_image(asset_dir, index)
    if local is not None:
        return local.read_bytes(), 0
    data = fetch(url)
    if not data:
        raise ValueError(f"empty body for {url}")
    stats["downloaded"] += 1
    return data, 1


def _store_image(asset_dir: Path, index: int, data: bytes, url: str) -> Path:
    path = asset_dir / f"{index:02d}{sniff_ext(data)}"
    for stale in asset_dir.glob(f"{index:02d}.*"):
        if stale != path:
            stale.unlink()
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return path


def _ocr_image(engine: Engine, path: Path) -> tuple[str | None, list[Block] | None, str | None]:
    try:
        blocks = engine(str(path))
    except Exception as exc:  # noqa: BLE001 - one bad image must not kill the run
        return None, None, repr(exc)
    text = "\n".join(block["text"] for block in blocks if block.get("text"))
    return text or None, blocks, None


def run(
    root: str | Path | None = None,
    *,
    family: str = "update",
    limit: int | None = None,
    engine: Engine | None = None,
    engine_meta: dict[str, str] | None = None,
    fetch: Fetcher | None = None,
    delay: float = 0.15,
) -> dict[str, int]:
    root_path = Path(root) if root else REPO_ROOT
    posts = load_posts(root_path, family)
    if limit is not None:
        posts = posts[:limit]
    if engine is None:
        engine, engine_meta = default_engine()
    if fetch is None:
        fetch = lambda url: http.fetch_bytes(url, headers={"Referer": "https://ds.163.com/"})  # noqa: E731

    derived_dir = root_path / "derived" / "ocr" / "dashen"
    derived_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "posts": 0,
        "images": 0,
        "downloaded": 0,
        "ocred": 0,
        "failed_images": 0,
        "failed_posts": 0,
        "written": 0,
    }

    for post in posts:
        feed_id = post["id"]
        try:
            asset_dir = root_path / "assets" / "dashen" / feed_id
            asset_dir.mkdir(parents=True, exist_ok=True)
            out_path = derived_dir / f"{feed_id}.json"
            existing: dict[str, Any] = {}
            if out_path.exists():
                existing = json.loads(out_path.read_text(encoding="utf-8"))
            cached: dict[int, dict[str, Any]] = {}
            if existing.get("engine") == engine_meta:
                # Same engine fingerprint: reuse per-image OCR by sha256.
                # A changed engine/meta invalidates every cached line.
                cached = {
                    image["index"]: image
                    for image in existing.get("images", [])
                    if image.get("sha256") and image.get("text") is not None
                }

            images: list[dict[str, Any]] = []
            for index, url in enumerate(image_urls_for(post), 1):
                stats["images"] += 1
                try:
                    data, downloaded = _image_bytes(asset_dir, index, url, fetch, stats)
                except Exception as exc:  # noqa: BLE001 - keep going, record the failure
                    images.append({"index": index, "url": url, "error": repr(exc)})
                    stats["failed_images"] += 1
                    continue
                if downloaded and delay:
                    time.sleep(delay)
                path = _store_image(asset_dir, index, data, url)
                digest = sha256_bytes(data)
                previous = cached.get(index)
                if previous and previous.get("sha256") == digest and previous.get("path") == path.relative_to(root_path).as_posix():
                    images.append(previous)
                    continue
                text, blocks, error = _ocr_image(engine, path)
                if error:
                    images.append(
                        {
                            "index": index,
                            "url": url,
                            "path": path.relative_to(root_path).as_posix(),
                            "sha256": digest,
                            "bytes": len(data),
                            "error": error,
                        }
                    )
                    stats["failed_images"] += 1
                    continue
                images.append(
                    {
                        "index": index,
                        "url": url,
                        "path": path.relative_to(root_path).as_posix(),
                        "sha256": digest,
                        "bytes": len(data),
                        "text": text,
                        "blocks": blocks,
                    }
                )
                stats["ocred"] += 1

            notice = {
                "id": f"dashen.{feed_id}",
                "family": family,
                "source": {
                    "published_at": post["published_at"],
                    "title": post["title"],
                    "url": post["feed_url"],
                },
                "engine": engine_meta,
                "images": images,
                "text": "\n\n".join(
                    image["text"] for image in images if image.get("text")
                ),
            }
            payload = json.dumps(notice, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if not out_path.exists() or out_path.read_text(encoding="utf-8") != payload:
                out_path.write_text(payload, encoding="utf-8", newline="\n")
                stats["written"] += 1
            stats["posts"] += 1
        except Exception as exc:  # noqa: BLE001 - one notice must not kill the run
            stats["failed_posts"] += 1
            print(f"[ocr] post failed {feed_id}: {exc!r}")
    return stats
