"""OCR pipeline for long-image update notices.

For every post in a label family (``study/labels.jsonl``), downloads its
images as original bytes under ``assets/dashen/<feed_id>/``, runs a local OCR
engine, and writes one JSON per notice under ``derived/ocr/dashen/<feed_id>.json``.
Corpus text is never modified; OCR output is a derived layer.

Engines: two rapidocr families share one windowed reader. ``build_engine("v6")``
(PP-OCRv6 small) is the default for new notices; ``build_engine("v4")``
(rapidocr-onnxruntime, PP-OCRv4) produced the committed 162 records and is the
independent second reader for new v6 notices. Both live behind extras
(``uv sync --all-extras``) so the base test suite stays dependency-light; the
imports are lazy and the engine is injectable for tests.

Reruns are idempotent: existing image bytes are not re-downloaded, and an
image whose ``sha256`` already has text in the notice JSON is not re-OCRed.
A truncated notice JSON is treated as absent and rebuilt, so a killed run
cannot wedge a notice.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import atomic, http
from .paths import REPO_ROOT

Fetcher = Callable[[str], bytes]
Block = dict[str, Any]
Engine = Callable[[str], list[Block]]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class LedgerMigrationError(RuntimeError):
    """``--force`` would re-OCR a notice whose correction ledger anchors another engine."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_ext(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(PNG_MAGIC):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] in (b"GIF8",):
        return ".gif"
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


def _rapidocr_v6():
    """Build the rapidocr 3.x (PP-OCRv6 small) engine, lazily.

    Same windowed reading as the v4 engine: tall images are sliced into
    overlapping 1280 px windows and per-window lines are merged, so results
    differ only in what the models read, not in how images are cut.
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
        for start, end in tiles(height):
            result = rapid(image[start:end, :], use_cls=False)
            lines.extend(raw_to_lines(result, start))
        return merge_lines(lines)

    meta = {
        "name": "rapidocr",
        "version": version("rapidocr"),
        "model": "PP-OCRv6-small",
        "tiling": "1280+256",
        "cls": "off",
    }
    return engine, meta


def _rapidocr_v4() -> tuple[Engine, dict[str, str]]:
    """Build the rapidocr-onnxruntime (PP-OCRv4) engine, lazily.

    This engine produced the committed 162 records. It stays available as the
    independent second reading when the record was made by v6.
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


ENGINE_BUILDERS: dict[str, Callable[[], tuple[Engine, dict[str, str]]]] = {
    "v4": _rapidocr_v4,
    "v6": _rapidocr_v6,
}


def build_engine(name: str) -> tuple[Engine, dict[str, str]]:
    """Build a named OCR engine: ``"v4"`` (PP-OCRv4) or ``"v6"`` (PP-OCRv6 small).

    This is the single construction point for both layers: :func:`default_engine`
    and :func:`v4_engine` are thin wrappers, and the audit runner asks for its
    second reader by name. An unknown name fails loudly instead of falling back
    to the default engine (which would turn the audit into a self-review).
    """
    try:
        builder = ENGINE_BUILDERS[name]
    except KeyError:
        choices = ", ".join(sorted(ENGINE_BUILDERS))
        raise ValueError(f"unknown OCR engine {name!r}; choose one of: {choices}") from None
    return builder()


def default_engine() -> tuple[Engine, dict[str, str]]:
    """Build the engine for NEW notices: rapidocr 3.x (PP-OCRv6 small).

    The 162 notices already on file keep their v4 records verbatim: run()
    skips any notice whose OCR was produced by a different, complete engine
    fingerprint, so switching the default never rewrites committed history.
    ``--force`` on the CLI is the explicit migration switch, and it refuses to
    touch a notice that already has a correction ledger until
    ``--migrate-ledger`` acknowledges that the ledger must be revalidated.
    The benchmark that chose v6 lives in the repo history: v6 fixes 3.4x more
    human-corrected hard blocks than v4 and reads full images twice as fast.

    Heavy dependency; imported lazily.
    """
    return build_engine("v6")


def v4_engine() -> tuple[Engine, dict[str, str]]:
    """The engine that produced the committed 162 notices (PP-OCRv4)."""
    return build_engine("v4")


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


def _read_existing(path: Path) -> dict[str, Any]:
    """Load a prior notice JSON; malformed output is treated as no cache.

    A killed process can leave a truncated file behind. Refusing the notice
    would wedge it forever, so the caller rebuilds it from the assets and the
    engine instead. Any other read error still propagates.
    """
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _refuse_ledger_migration(
    base: Path,
    posts: list[dict[str, Any]],
    derived_dir: Path,
    engine_meta: dict[str, str] | None,
) -> None:
    """Refuse a cross-engine ``--force`` while correction ledgers still anchor OCR.

    Checked before any file is written (the asset and output directories are
    untouched), so an accidental engine switch cannot silently invalidate
    ledger locators. ``--migrate-ledger`` is the explicit override; the
    operator then revalidates the ledgers after the re-OCR.
    """
    if engine_meta is None:
        return
    blocked: list[str] = []
    for post in posts:
        feed = post["id"]
        existing = _read_existing(derived_dir / f"{feed}.json")
        if not existing or existing.get("engine") == engine_meta:
            continue
        if (base / "corrections" / "dashen" / f"{feed}.json").is_file():
            blocked.append(feed)
    if not blocked:
        return
    shown = ", ".join(blocked[:5])
    more = f" (+{len(blocked) - 5} more)" if len(blocked) > 5 else ""
    raise LedgerMigrationError(
        f"--force would re-OCR {len(blocked)} notice(s) under a different engine "
        f"than their correction ledger anchors: {shown}{more}; revalidate those "
        f"ledgers, then rerun with --migrate-ledger"
    )


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
        atomic.write_bytes(path, data)
    return path


def _ocr_image(engine: Engine, path: Path) -> tuple[str, list[Block], str | None]:
    try:
        blocks = engine(str(path))
    except Exception as exc:  # noqa: BLE001 - one bad image must not kill the run
        return "", [], repr(exc)
    text = "\n".join(block["text"] for block in blocks if block.get("text"))
    # Empty string means "OCR ran, found no text" — cacheable, unlike a
    # missing result (None) from an image that was never processed.
    return text, blocks, None


def run(
    root: str | Path | None = None,
    *,
    family: str = "update",
    limit: int | None = None,
    engine: Engine | None = None,
    engine_meta: dict[str, str] | None = None,
    fetch: Fetcher | None = None,
    delay: float = 0.15,
    out_dir: str | Path | None = None,
    download: bool = True,
    force: bool = False,
    migrate_ledger: bool = False,
) -> dict[str, int]:
    root_path = Path(root) if root else REPO_ROOT
    posts = load_posts(root_path, family)
    if limit is not None:
        posts = posts[:limit]
    if engine is None:
        engine, engine_meta = default_engine()
    if fetch is None:
        if download:
            fetch = lambda url: http.fetch_bytes(url, headers={"Referer": "https://ds.163.com/"})  # noqa: E731
        else:
            def fetch(url: str) -> bytes:
                raise ValueError(f"download disabled: {url}")

    derived_dir = (root_path / out_dir) if out_dir else (root_path / "derived" / "ocr" / "dashen")
    if force and not migrate_ledger:
        # Preflight before any directory or file is created.
        _refuse_ledger_migration(root_path, posts, derived_dir, engine_meta)
    derived_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "posts": 0,
        "images": 0,
        "downloaded": 0,
        "ocred": 0,
        "failed_images": 0,
        "failed_posts": 0,
        "written": 0,
        "kept_foreign": 0,
    }

    for post in posts:
        feed_id = post["id"]
        try:
            asset_dir = root_path / "assets" / "dashen" / feed_id
            asset_dir.mkdir(parents=True, exist_ok=True)
            out_path = derived_dir / f"{feed_id}.json"
            existing = _read_existing(out_path)
            if (
                existing
                and not force
                and existing.get("engine") != engine_meta
                and existing.get("images")
                and all(
                    image.get("text") is not None and not image.get("error")
                    for image in existing["images"]
                )
            ):
                # A complete record from another engine stays untouched:
                # committed OCR history never gets rewritten by a default
                # engine switch. Use force for an explicit migration.
                stats["kept_foreign"] += 1
                stats["posts"] += 1
                continue
            cached: dict[int, dict[str, Any]] = {}
            if existing.get("engine") == engine_meta:
                # Same engine fingerprint: reuse per-image OCR by sha256, even
                # with --force. A force retry therefore re-OCRs only failed or
                # missing images; a changed engine/meta invalidates every
                # cached line.
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
                atomic.write_text(out_path, payload)
                stats["written"] += 1
            stats["posts"] += 1
        except Exception as exc:  # noqa: BLE001 - one notice must not kill the run
            stats["failed_posts"] += 1
            print(f"[ocr] post failed {feed_id}: {exc!r}")
    return stats
