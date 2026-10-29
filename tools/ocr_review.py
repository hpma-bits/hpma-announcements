#!/usr/bin/env python3
"""Tooling for the manual OCR text review.

The OCR layer under ``derived/ocr/dashen/`` is immutable engine output.  This
tool supports a human/agent review on top of it:

    queue    write one compact text file per notice (block ids + scores + text)
    batches  concatenate notices into balanced review batches
    crop     render a tight, upscaled crop of one OCR block from its source image
    verify   check a corrections ledger against the raw OCR
    apply    materialise the corrected layer under ``derived/ocr/corrected/``

Corrections are stored per notice under ``derived/ocr/corrections/dashen/`` and
locate text by ``(image_index, block_index)`` plus the original string, so a
stale or mismatched correction fails loudly instead of silently editing the
wrong line.

Only Pillow (already an OCR dependency) is needed, and only for ``crop``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = Path("derived/ocr/dashen")
CORRECTION_DIR = Path("derived/ocr/corrections/dashen")
CORRECTED_DIR = Path("derived/ocr/corrected/dashen")
REVIEW_DIR = Path(".review")
CROP_DIR = REVIEW_DIR / "crops"
QUEUE_DIR = REVIEW_DIR / "queue"
BATCH_DIR = REVIEW_DIR / "batches"


class ReviewError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def notice_path(feed_id: str) -> Path:
    feed_id = feed_id.removeprefix("dashen.")
    return REPO_ROOT / OCR_DIR / f"{feed_id}.json"


def load_notice(feed_id: str) -> dict[str, Any]:
    path = notice_path(feed_id)
    if not path.exists():
        raise ReviewError(f"unknown notice: {feed_id}")
    return read_json(path)


def iter_notices() -> Iterable[tuple[str, dict[str, Any]]]:
    for path in sorted((REPO_ROOT / OCR_DIR).glob("*.json")):
        yield path.stem, read_json(path)


def block_box(block: dict[str, Any]) -> tuple[int, int, int, int]:
    xs = [int(point[0]) for point in block["box"]]
    ys = [int(point[1]) for point in block["box"]]
    return min(xs), min(ys), max(xs), max(ys)


def queue_text(feed_id: str, notice: dict[str, Any]) -> str:
    source = notice.get("source", {})
    lines = [
        f"===== NOTICE dashen.{feed_id} | {source.get('published_at', '')} | "
        f"{source.get('title', '')}",
    ]
    for image in notice.get("images", []):
        if image.get("error"):
            lines.append(
                f"IMG {image.get('index')} | {image.get('path', '')} | ERROR {image['error']}"
            )
            continue
        blocks = image.get("blocks", [])
        lines.append(f"IMG {image.get('index')} | {image.get('path', '')} | {len(blocks)} blocks")
        for index, block in enumerate(blocks):
            lines.append(f"b{index:04d} {float(block.get('score', 0.0)):.4f} | {block.get('text', '')}")
    return "\n".join(lines) + "\n"


def cmd_queue(args: argparse.Namespace) -> int:
    out_dir = Path(args.out) if args.out else REPO_ROOT / QUEUE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for feed_id, notice in iter_notices():
        (out_dir / f"{feed_id}.txt").write_text(
            queue_text(feed_id, notice), encoding="utf-8", newline="\n"
        )
        count += 1
    print(f"wrote {count} queue files to {out_dir}")
    return 0


def cmd_batches(args: argparse.Namespace) -> int:
    out_dir = Path(args.out) if args.out else REPO_ROOT / BATCH_DIR
    queue_dir = Path(args.queue) if args.queue else REPO_ROOT / QUEUE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    notices = [(feed_id, notice) for feed_id, notice in iter_notices()]
    if not notices:
        raise ReviewError("no OCR notices found")
    weights = {
        feed_id: sum(len(image.get("blocks", [])) for image in notice.get("images", []))
        for feed_id, notice in notices
    }
    count = max(1, min(args.count, len(notices)))
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for feed_id in sorted(weights, key=lambda item: (-weights[item], item)):
        target = min(range(count), key=lambda index: (loads[index], index))
        buckets[target].append(feed_id)
        loads[target] += weights[feed_id]
    manifests = []
    for number, bucket in enumerate(buckets, 1):
        name = f"batch-{number:02d}"
        parts = []
        for feed_id in sorted(bucket, key=lambda item: load_notice(item)["source"]["published_at"]):
            part = queue_dir / f"{feed_id}.txt"
            if not part.exists():
                raise ReviewError(f"missing queue file {part}; run the queue command first")
            parts.append(part.read_text(encoding="utf-8"))
        body = "\n".join(parts)
        (out_dir / f"{name}.txt").write_text(body, encoding="utf-8", newline="\n")
        manifests.append(
            {
                "batch": name,
                "file": (out_dir / f"{name}.txt").relative_to(REPO_ROOT).as_posix(),
                "notices": sorted(bucket),
                "blocks": loads[number - 1],
                "proposal_file": f".review/proposals/{name}.jsonl",
            }
        )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifests, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for row in manifests:
        print(f"{row['batch']}: {len(row['notices'])} notices, {row['blocks']} blocks")
    return 0


def same_line(block: dict[str, Any], other: dict[str, Any]) -> bool:
    _, top, _, bottom = block_box(block)
    _, otop, _, obottom = block_box(other)
    overlap = min(bottom, obottom) - max(top, otop)
    height = min(bottom - top, obottom - otop)
    return height > 0 and overlap / height >= 0.5


def render_block_crop(
    notice: dict[str, Any],
    image_index: int,
    block_index: int,
    *,
    context: int = 0,
    pad_x: int = 40,
    pad_y: int = 12,
    target_width: float = 1400.0,
    scale: float = 0.0,
) -> tuple[Any, list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    """Return ``(PIL image, context blocks, image record)`` for one block."""
    from PIL import Image

    images = notice.get("images", [])
    if not 1 <= image_index <= len(images):
        raise ReviewError(f"image index out of range: {image_index}")
    image_record = images[image_index - 1]
    if image_record.get("error"):
        raise ReviewError(f"image has an OCR error: {image_record['error']}")
    blocks = image_record.get("blocks", [])
    if not 0 <= block_index < len(blocks):
        raise ReviewError(f"block index out of range: {block_index}")

    target = blocks[block_index]
    left, top, right, bottom = block_box(target)
    for block in blocks:
        if same_line(block, target):
            bl, _, br, _ = block_box(block)
            left, right = min(left, bl), max(right, br)
    left = max(0, left - pad_x)
    right += pad_x
    top = max(0, top - pad_y)
    bottom += pad_y

    # Vertical context: only tightly adjacent lines, so a page gap cannot
    # balloon the crop into the whole image.
    ordered = sorted(
        enumerate(blocks), key=lambda item: (block_box(item[1])[1], block_box(item[1])[0])
    )
    position = next(pos for pos, (index, _) in enumerate(ordered) if index == block_index)
    first = position
    for offset in range(1, context + 1):
        above = position - offset
        if above < 0:
            break
        gap = top - block_box(ordered[above][1])[3]
        if gap > 80:
            break
        first = above
    last = position
    for offset in range(1, context + 1):
        below = position + offset
        if below >= len(ordered):
            break
        gap = block_box(ordered[below][1])[1] - bottom
        if gap > 80:
            break
        last = below
    context_slice = ordered[first : last + 1]
    if context:
        top = min(top, block_box(context_slice[0][1])[1] - pad_y)
        bottom = max(bottom, block_box(context_slice[-1][1])[3] + pad_y)

    image_path = REPO_ROOT / image_record["path"]
    if not image_path.exists():
        raise ReviewError(f"missing source image: {image_path}")
    source = Image.open(image_path).convert("RGB")
    crop = source.crop((left, top, right, bottom))
    width, height = crop.size
    if scale <= 0:
        scale = max(1.0, min(4.0, target_width / max(width, 1)))
    if scale != 1.0:
        crop = crop.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.LANCZOS,
        )
    return crop, [(index, blocks[index]) for index, _ in context_slice], image_record


def cmd_crop(args: argparse.Namespace) -> int:
    notice = load_notice(args.notice)
    crop, context_blocks, image_record = render_block_crop(
        notice,
        args.image,
        args.block,
        context=args.context,
        pad_x=args.pad_x,
        pad_y=args.pad_y,
        target_width=args.target_width,
        scale=args.scale,
    )
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
    else:
        feed_id = args.notice.removeprefix("dashen.")
        out_path = REPO_ROOT / CROP_DIR / feed_id / f"img{args.image:02d}-b{args.block:04d}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path, format="PNG", optimize=False)

    print(f"crop: {out_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"image: {image_record['path']} ({crop.width}x{crop.height})")
    for index, block in context_blocks:
        mark = ">>" if index == args.block else "  "
        print(f"{mark} b{index:04d} {float(block.get('score', 0.0)):.4f} | {block.get('text', '')}")
    return 0


def _label_font(size: int):
    from PIL import ImageFont

    for candidate in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def corpus_text() -> str:
    parts = []
    path = REPO_ROOT / "corpus" / "dashen" / "posts.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                post = json.loads(line)
                parts.append(
                    (post.get("title") or "")
                    + "\n"
                    + (post.get("text") or "")
                    + "\n"
                    + (post.get("long_text") or "")
                )
    return "\n".join(parts)


def cmd_term(args: argparse.Namespace) -> int:
    corpus = corpus_text()
    ocr_hits = {term: [] for term in args.terms}
    for feed_id, notice in iter_notices():
        for image in notice.get("images", []):
            for index, block in enumerate(image.get("blocks", [])):
                text = block.get("text", "")
                for term in args.terms:
                    if term in text:
                        ocr_hits[term].append((feed_id, image["index"], index, block.get("score", 0)))
    for term in args.terms:
        hits = ocr_hits[term]
        print(f"{term}: corpus={corpus.count(term)} ocr={len(hits)}")
        for feed_id, image_index, block_index, score in hits[: args.limit]:
            print(f"  dashen.{feed_id} img{image_index} b{block_index} score={float(score):.4f}")
        if len(hits) > args.limit:
            print(f"  ... {len(hits) - args.limit} more")
    return 0


def cmd_grep(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for feed_id, notice in iter_notices():
        for image in notice.get("images", []):
            if image.get("error"):
                continue
            for index, block in enumerate(image.get("blocks", [])):
                text = block.get("text", "")
                if any(pattern in text for pattern in args.patterns):
                    rows.append(
                        {
                            "notice_id": f"dashen.{feed_id}",
                            "image_index": image["index"],
                            "block_index": index,
                            "original": text,
                            "score": float(block.get("score", 0.0)),
                        }
                    )
    rows.sort(key=lambda row: (row["notice_id"], row["image_index"], row["block_index"]))
    out_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(rows)} blocks to {out_path.relative_to(REPO_ROOT).as_posix()}")
    return 0


def batch_notices(batch: str) -> set[str]:
    manifest_path = REPO_ROOT / BATCH_DIR / "manifest.json"
    if not manifest_path.exists():
        raise ReviewError("missing batch manifest; run the batches command first")
    for row in read_json(manifest_path):
        if row["batch"] == batch:
            return {f"dashen.{feed_id}" for feed_id in row["notices"]}
    raise ReviewError(f"unknown batch: {batch}")


def cmd_candidates(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    allowed = batch_notices(args.batch) if args.batch else None
    rows = []
    for feed_id, notice in iter_notices():
        if allowed is not None and f"dashen.{feed_id}" not in allowed:
            continue
        for image in notice.get("images", []):
            if image.get("error"):
                continue
            for index, block in enumerate(image.get("blocks", [])):
                score = float(block.get("score", 0.0))
                text = block.get("text", "")
                if score > args.max_score:
                    continue
                if args.contains and not any(char in text for char in args.contains):
                    continue
                rows.append(
                    {
                        "notice_id": f"dashen.{feed_id}",
                        "image_index": image["index"],
                        "block_index": index,
                        "original": text,
                        "score": score,
                    }
                )
    rows.sort(key=lambda row: (row["score"], row["notice_id"], row["image_index"], row["block_index"]))
    out_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(rows)} candidates to {out_path.relative_to(REPO_ROOT).as_posix()}")
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    from PIL import Image, ImageDraw

    proposals_path = Path(args.proposals)
    if not proposals_path.is_absolute():
        proposals_path = REPO_ROOT / proposals_path
    rows = []
    for line in proposals_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if not rows:
        raise ReviewError("no proposals to render")
    per_sheet = max(1, args.per_sheet)
    out_dir = proposals_path.parent / "sheets"
    out_dir.mkdir(parents=True, exist_ok=True)
    width = args.width
    written = []
    for sheet_number, start in enumerate(range(0, len(rows), per_sheet), 1):
        chunk = rows[start : start + per_sheet]
        prepared = []
        for offset, row in enumerate(chunk, 1):
            notice = load_notice(row["notice_id"])
            crop, _, _ = render_block_crop(
                notice,
                int(row["image_index"]),
                int(row["block_index"]),
                context=0,
                pad_x=30,
                pad_y=8,
                target_width=width - 140,
            )
            if crop.width < width - 140:
                canvas = Image.new("RGB", (width - 140, crop.height), "white")
                canvas.paste(crop, (0, 0))
                crop = canvas
            elif crop.width > width - 140:
                ratio = (width - 140) / crop.width
                crop = crop.resize((width - 140, max(1, round(crop.height * ratio))), Image.LANCZOS)
            prepared.append((offset, crop))
        gap = 10
        font = _label_font(17)
        total_height = 0
        for row_index, (_, crop) in enumerate(prepared):
            has_fix = bool(chunk[row_index].get("corrected"))
            total_height += (74 if has_fix else 48) + crop.height + gap
        total_height += gap
        sheet = Image.new("RGB", (width, total_height), "white")
        draw = ImageDraw.Draw(sheet)

        def fit(text: str) -> str:
            while text and draw.textlength(text, font=font) > width - 16:
                text = text[:-1]
            return text

        y = gap
        for offset, crop in prepared:
            row = chunk[offset - 1]
            score = row.get("score")
            suffix = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
            label = (
                f"#{start + offset} {row['notice_id']} img{row['image_index']} "
                f"b{row['block_index']}{suffix}"
            )
            draw.text((8, y + 3), label, fill="black", font=font)
            draw.text((8, y + 24), fit(f"OCR: {row.get('original') or ''}"), fill=(150, 0, 0), font=font)
            if row.get("corrected"):
                draw.text((8, y + 45), fit(f"FIX: {row['corrected']}"), fill=(0, 90, 0), font=font)
                y += 74
            else:
                y += 48
            sheet.paste(crop, (0, y))
            y += crop.height + gap
        out_path = out_dir / f"{proposals_path.stem}-{sheet_number:02d}.png"
        sheet.save(out_path, format="PNG")
        written.append(out_path.relative_to(REPO_ROOT).as_posix())
    for path in written:
        print(f"sheet: {path}")
    return 0


def load_ledger(feed_id: str) -> dict[str, Any] | None:
    path = REPO_ROOT / CORRECTION_DIR / f"{feed_id.removeprefix('dashen.')}.json"
    if not path.exists():
        return None
    return read_json(path)


def check_ledger(notice: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    seen: set[tuple[int, int]] = set()
    corrections = ledger.get("corrections")
    if not isinstance(corrections, list):
        return ["corrections must be a list"]
    images = notice.get("images", [])
    for number, correction in enumerate(corrections):
        label = f"correction[{number}]"
        image_index = correction.get("image_index")
        block_index = correction.get("block_index")
        if not isinstance(image_index, int) or not isinstance(block_index, int):
            problems.append(f"{label}: image_index/block_index must be integers")
            continue
        key = (image_index, block_index)
        if key in seen:
            problems.append(f"{label}: duplicate locator {key}")
        seen.add(key)
        if not 1 <= image_index <= len(images):
            problems.append(f"{label}: image_index {image_index} out of range")
            continue
        blocks = images[image_index - 1].get("blocks", [])
        if not 0 <= block_index < len(blocks):
            problems.append(f"{label}: block_index {block_index} out of range")
            continue
        original = correction.get("original")
        corrected = correction.get("corrected")
        if original != blocks[block_index].get("text"):
            problems.append(
                f"{label}: original mismatch at image {image_index} block {block_index}: "
                f"ledger={original!r} ocr={blocks[block_index].get('text')!r}"
            )
        if not isinstance(corrected, str) or not corrected:
            problems.append(f"{label}: corrected must be a non-empty string")
        elif corrected == original:
            problems.append(f"{label}: corrected equals original")
    return problems


def cmd_verify(args: argparse.Namespace) -> int:
    ledger_dir = Path(args.ledger) if args.ledger else REPO_ROOT / CORRECTION_DIR
    files = sorted(ledger_dir.glob("*.json"))
    total = 0
    problems = 0
    for path in files:
        ledger = read_json(path)
        feed_id = ledger.get("id") or path.stem
        try:
            notice = load_notice(feed_id)
        except ReviewError as exc:
            print(f"{path.name}: {exc}")
            problems += 1
            continue
        issues = check_ledger(notice, ledger)
        total += len(ledger.get("corrections", []))
        for issue in issues:
            print(f"{path.name}: {issue}")
        problems += len(issues)
    print(f"verified {len(files)} ledgers, {total} corrections, {problems} problems")
    return 1 if problems else 0


def cmd_apply(args: argparse.Namespace) -> int:
    ledger_dir = Path(args.ledger) if args.ledger else REPO_ROOT / CORRECTION_DIR
    out_dir = Path(args.out) if args.out else REPO_ROOT / CORRECTED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    corrections = 0
    for path in sorted(ledger_dir.glob("*.json")):
        ledger = read_json(path)
        feed_id = ledger.get("id") or path.stem
        notice = load_notice(feed_id)
        issues = check_ledger(notice, ledger)
        if issues:
            for issue in issues:
                print(f"{path.name}: {issue}")
            raise ReviewError(f"refusing to apply invalid ledger {path}")
        by_image: dict[int, dict[int, str]] = {}
        for correction in ledger["corrections"]:
            by_image.setdefault(correction["image_index"], {})[correction["block_index"]] = (
                correction["corrected"]
            )
        corrected_notice = json.loads(json.dumps(notice))
        for image_index, replacements in by_image.items():
            image = corrected_notice["images"][image_index - 1]
            for block_index, text in replacements.items():
                image["blocks"][block_index]["text"] = text
            image["text"] = "\n".join(
                block["text"] for block in image["blocks"] if block.get("text")
            )
            corrections += len(replacements)
        corrected_notice["text"] = "\n\n".join(
            image["text"] for image in corrected_notice["images"] if image.get("text")
        )
        corrected_notice["corrections_applied"] = sum(len(item) for item in by_image.values())
        payload = json.dumps(corrected_notice, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        out_path = out_dir / f"{feed_id.removeprefix('dashen.')}.json"
        if not out_path.exists() or out_path.read_text(encoding="utf-8") != payload:
            out_path.write_text(payload, encoding="utf-8", newline="\n")
            written += 1
    print(f"applied {corrections} corrections from {len(list(ledger_dir.glob('*.json')))} ledgers to {written} notices")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    queue = sub.add_parser("queue", help="write compact per-notice review queues")
    queue.add_argument("--out", default=None)
    queue.set_defaults(func=cmd_queue)

    batches = sub.add_parser("batches", help="split notices into balanced review batches")
    batches.add_argument("--count", type=int, default=10)
    batches.add_argument("--queue", default=None)
    batches.add_argument("--out", default=None)
    batches.set_defaults(func=cmd_batches)

    crop = sub.add_parser("crop", help="render a source-image crop for one block")
    crop.add_argument("notice")
    crop.add_argument("image", type=int)
    crop.add_argument("block", type=int)
    crop.add_argument("--context", type=int, default=0)
    crop.add_argument("--pad-x", type=int, default=40)
    crop.add_argument("--pad-y", type=int, default=12)
    crop.add_argument("--scale", type=float, default=0.0)
    crop.add_argument("--target-width", type=float, default=1400.0)
    crop.add_argument("--out", default=None)
    crop.set_defaults(func=cmd_crop)

    term = sub.add_parser("term", help="count a term in the clean corpus and OCR blocks")
    term.add_argument("terms", nargs="+")
    term.add_argument("--limit", type=int, default=20)
    term.set_defaults(func=cmd_term)

    grep = sub.add_parser("grep", help="list OCR blocks containing any of the patterns")
    grep.add_argument("patterns", nargs="+")
    grep.add_argument("--out", required=True)
    grep.set_defaults(func=cmd_grep)

    candidates = sub.add_parser("candidates", help="list low-confidence blocks as review candidates")
    candidates.add_argument("--max-score", type=float, default=0.90)
    candidates.add_argument("--contains", default="")
    candidates.add_argument("--batch", default=None)
    candidates.add_argument("--out", required=True)
    candidates.set_defaults(func=cmd_candidates)

    sheet = sub.add_parser("sheet", help="render a labelled contact sheet for proposals")
    sheet.add_argument("--proposals", required=True)
    sheet.add_argument("--per-sheet", type=int, default=12)
    sheet.add_argument("--width", type=int, default=1200)
    sheet.set_defaults(func=cmd_sheet)

    verify = sub.add_parser("verify", help="check correction ledgers against raw OCR")
    verify.add_argument("--ledger", default=None)
    verify.set_defaults(func=cmd_verify)

    apply = sub.add_parser("apply", help="write the corrected OCR layer")
    apply.add_argument("--ledger", default=None)
    apply.add_argument("--out", default=None)
    apply.set_defaults(func=cmd_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
