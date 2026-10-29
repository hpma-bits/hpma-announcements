#!/usr/bin/env python3
"""Build the static OCR review page under ``checkpage/``.

The page is a human-verification front end for the derived OCR layer: every
notice image is shown next to its OCR lines, with boxes, confidence bands and
per-image corrections. Review state lives in the browser; this script only
produces the read-only inputs:

    checkpage/index.html   UI shell with CSS/JS inlined
    checkpage/data.js      window.CHECKPAGE_DATA = {...}

Sources are the committed corpus, labels, OCR output and original images. The
build is deterministic (no timestamps, stable ordering) and stdlib-only.
``--check`` exits non-zero when the generated files are stale.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = Path(__file__).resolve().parent / "checkpage"

DATA_VERSION = 1
LOW_SCORE = 0.90
VERY_LOW_SCORE = 0.80

SOURCES = {
    "corpus": "corpus/dashen/posts.jsonl",
    "labels": "study/labels.jsonl",
    "ocr": "derived/ocr/dashen",
}


class BuildError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def image_size(path: Path) -> tuple[int, int] | None:
    """Return (width, height) from image headers, without Pillow.

    Supports PNG, GIF, JPEG and WebP (VP8 / VP8L / VP8X), which covers every
    asset in the archive. Returns None for anything unreadable.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
            if len(head) < 16:
                return None
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp_size(head)
            if head[:3] == b"\xff\xd8\xff":
                return _jpeg_size(handle)
    except OSError:
        return None
    return None


def _webp_size(head: bytes) -> tuple[int, int] | None:
    chunk = head[12:16]
    if chunk == b"VP8 " and len(head) >= 30:
        width, height = struct.unpack("<HH", head[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(head) >= 25 and head[20] == 0x2F:
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(head) >= 30:
        width = int.from_bytes(head[24:27], "little") + 1
        height = int.from_bytes(head[27:30], "little") + 1
        return width, height
    return None


def _jpeg_size(handle) -> tuple[int, int] | None:
    handle.seek(2)
    while True:
        byte = handle.read(1)
        if not byte:
            return None
        if byte != b"\xff":
            continue
        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if not marker:
            return None
        code = marker[0]
        if code in (0xD8, 0xD9):
            continue
        raw = handle.read(2)
        if len(raw) < 2:
            return None
        length = struct.unpack(">H", raw)[0]
        if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
            data = handle.read(5)
            if len(data) < 5:
                return None
            height, width = struct.unpack(">HH", data[1:5])
            return width, height
        if length < 2:
            return None
        handle.seek(length - 2, 1)


def _blocks(block_list: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for block in block_list:
        box = block.get("box") or []
        points = [point for point in box if len(point) >= 2]
        if not points:
            continue
        xs = [int(point[0]) for point in points]
        ys = [int(point[1]) for point in points]
        out.append(
            {
                "x": min(xs),
                "y": min(ys),
                "w": max(xs) - min(xs),
                "h": max(ys) - min(ys),
                "text": block.get("text") or "",
                "score": round(float(block.get("score") or 0.0), 3),
            }
        )
    return out


def _image_stats(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [block["score"] for block in blocks]
    return {
        "blocks": len(blocks),
        "chars": sum(len(block["text"]) for block in blocks),
        "mean": round(sum(scores) / len(scores), 3) if scores else None,
        "low": sum(1 for score in scores if score < LOW_SCORE),
        "veryLow": sum(1 for score in scores if score < VERY_LOW_SCORE),
    }


def _relative(path: Path, out_dir: Path) -> str:
    try:
        return Path(os.path.relpath(path, out_dir)).as_posix()
    except ValueError:  # different drives; never expected in-repo
        return path.as_posix()


def collect(root: Path, out_dir: Path) -> tuple[dict[str, Any], list[str]]:
    posts = {post["id"]: post for post in read_jsonl(root / SOURCES["corpus"])}
    labels = {row["id"]: row for row in read_jsonl(root / SOURCES["labels"])}
    ocr_dir = root / SOURCES["ocr"]
    if not ocr_dir.is_dir():
        raise BuildError(f"missing OCR directory: {ocr_dir} (run `python -m collectors.cli ocr` first)")

    warnings: list[str] = []
    notices: list[dict[str, Any]] = []
    engines: dict[str, dict[str, Any]] = {}

    for path in sorted(ocr_dir.glob("*.json")):
        record = read_json(path)
        full_id = record.get("id") or ""
        source, _, feed_id = full_id.partition(".")
        if source != "dashen" or not feed_id:
            warnings.append(f"{path.name}: unexpected id {full_id!r}")
            continue

        post = posts.get(feed_id, {})
        label = labels.get(feed_id, {})
        source_meta = record.get("source") or {}
        engine = record.get("engine") or {}
        if engine:
            engines[json.dumps(engine, ensure_ascii=False, sort_keys=True)] = engine

        images = []
        issues: list[str] = []
        for image in sorted(record.get("images") or [], key=lambda item: item.get("index", 0)):
            rel_path = image.get("path") or ""
            abs_path = root / rel_path if rel_path else None
            missing = abs_path is None or not abs_path.exists()
            size = None if missing else image_size(abs_path)
            if missing:
                issues.append(f"缺图 {image.get('index')}")
                warnings.append(f"{feed_id}: missing image {rel_path or '(no path)'}")
            elif size is None:
                issues.append(f"图 {image.get('index')} 尺寸未知")
                warnings.append(f"{feed_id}: cannot read image size {rel_path}")

            blocks = _blocks(image.get("blocks") or [])
            if image.get("error"):
                issues.append(f"图 {image.get('index')} OCR 失败")
                warnings.append(f"{feed_id}: OCR failed for image {image.get('index')}: {image.get('error')}")
            images.append(
                {
                    "index": int(image.get("index") or 0),
                    "src": _relative(abs_path, out_dir) if abs_path else "",
                    "path": rel_path,
                    "url": image.get("url") or "",
                    "bytes": int(image.get("bytes") or 0),
                    "sha256": image.get("sha256") or "",
                    "width": size[0] if size else None,
                    "height": size[1] if size else None,
                    "missing": missing,
                    "text": image.get("text") or "",
                    "error": image.get("error"),
                    "blocks": blocks,
                    "stats": _image_stats(blocks),
                }
            )

        all_blocks = [block for image in images for block in image["blocks"]]
        scores = [block["score"] for block in all_blocks]
        text = record.get("text") or ""
        if not text:
            issues.append("OCR 文本为空")
        empty_images = sum(1 for image in images if not image["blocks"])
        if empty_images:
            issues.append(f"{empty_images} 张图未识别到文字")

        stats = {
            "images": len(images),
            "blocks": len(all_blocks),
            "chars": len(text),
            "mean": round(sum(scores) / len(scores), 3) if scores else None,
            "low": sum(1 for score in scores if score < LOW_SCORE),
            "veryLow": sum(1 for score in scores if score < VERY_LOW_SCORE),
            "emptyImages": empty_images,
        }

        notices.append(
            {
                "id": full_id,
                "feedId": feed_id,
                "title": post.get("title") or source_meta.get("title") or full_id,
                "publishedAt": post.get("published_at") or source_meta.get("published_at") or "",
                "sourceUpdatedAt": post.get("source_updated_at") or "",
                "url": post.get("feed_url") or source_meta.get("url") or f"https://ds.163.com/feed/{feed_id}/",
                "postType": post.get("type"),
                "topics": post.get("topics") or [],
                "deleted": bool(post.get("deleted")),
                "noticeDate": label.get("notice_date"),
                "label": label.get("label") or record.get("family") or "unknown",
                "labelConfidence": label.get("confidence"),
                "labelEvidence": label.get("evidence"),
                "bodyText": post.get("text") or "",
                "ocrText": text,
                "images": images,
                "issues": issues,
                "stats": stats,
            }
        )

    notices.sort(key=lambda notice: (notice["publishedAt"], notice["id"]))
    if not notices:
        raise BuildError("no OCR records found")

    summary = {
        "notices": len(notices),
        "images": sum(notice["stats"]["images"] for notice in notices),
        "blocks": sum(notice["stats"]["blocks"] for notice in notices),
        "chars": sum(notice["stats"]["chars"] for notice in notices),
        "lowBlocks": sum(notice["stats"]["low"] for notice in notices),
        "veryLowBlocks": sum(notice["stats"]["veryLow"] for notice in notices),
        "missingImages": sum(1 for notice in notices for image in notice["images"] if image["missing"]),
        "noticesWithIssues": sum(1 for notice in notices if notice["issues"]),
    }

    payload = {
        "version": DATA_VERSION,
        "thresholds": {"low": LOW_SCORE, "veryLow": VERY_LOW_SCORE},
        "sources": SOURCES,
        "engines": list(engines.values()),
        "summary": summary,
        "notices": notices,
    }
    return payload, warnings


def data_js(payload: dict[str, Any]) -> str:
    return (
        "window.CHECKPAGE_DATA="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )


def render_html() -> str:
    template = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    style = (TEMPLATE_DIR / "style.css").read_text(encoding="utf-8")
    app = (TEMPLATE_DIR / "app.js").read_text(encoding="utf-8")
    if "/*{{STYLE}}*/" not in template or "/*{{APP}}*/" not in template:
        raise BuildError("template is missing the /*{{STYLE}}*/ or /*{{APP}}*/ marker")
    return template.replace("/*{{STYLE}}*/", style).replace("/*{{APP}}*/", app)


def _write(path: Path, content: str) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return True


def build(root: Path, out_dir: Path, check: bool = False) -> tuple[dict[str, Any], list[str], dict[str, bool]]:
    payload, warnings = collect(root, out_dir)
    outputs = {"index.html": render_html(), "data.js": data_js(payload)}
    changed = {}
    for name, content in outputs.items():
        path = out_dir / name
        differs = not path.exists() or path.read_text(encoding="utf-8") != content
        changed[name] = differs
        if differs and not check:
            _write(path, content)
    return payload, warnings, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build the OCR review checkpage")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: <root>/checkpage)")
    parser.add_argument("--check", action="store_true", help="fail if the generated files are stale")
    parser.add_argument("--quiet", action="store_true", help="only print errors")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out_dir = (args.out or (root / "checkpage")).resolve()
    try:
        payload, warnings, changed = build(root, out_dir, check=args.check)
    except BuildError as exc:
        print(f"checkpage: {exc}", file=sys.stderr)
        return 2

    summary = payload["summary"]
    stale = [name for name, differs in changed.items() if differs]
    if not args.quiet:
        print(
            f"checkpage: {summary['notices']} notices, {summary['images']} images, "
            f"{summary['blocks']:,} lines, {summary['chars']:,} chars"
        )
        print(
            f"  confidence: {summary['lowBlocks']} low (<{LOW_SCORE}), "
            f"{summary['veryLowBlocks']} very low (<{VERY_LOW_SCORE}); "
            f"{summary['noticesWithIssues']} notices with build issues"
        )
        if warnings:
            print(f"  warnings ({len(warnings)}):")
            for warning in warnings[:10]:
                print(f"    - {warning}")
            if len(warnings) > 10:
                print(f"    ... {len(warnings) - 10} more")
        if args.check:
            print("  stale: " + (", ".join(stale) if stale else "none"))
        else:
            print("  wrote: " + (", ".join(stale) if stale else "nothing (up to date)"))
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
