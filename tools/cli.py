"""Command-line entry point for the archive's local tooling.

    python -m tools.cli verify
    python -m tools.cli crop <notice> <image> <block> [--context N] [--scale F]
    python -m tools.cli edition [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import archive, crop, edition

CROP_DIR = Path(".review/crops")


class CliError(RuntimeError):
    """A request the committed data cannot satisfy."""


def _verify(args: argparse.Namespace) -> int:
    ledgers = list(archive.iter_ledgers())
    corrections = 0
    problems = 0
    for feed, ledger in ledgers:
        corrections += len(ledger.get("corrections", []))
        try:
            notice = archive.load_ocr(feed)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            problems += 1
            continue
        issues = archive.check_ledger(notice, ledger)
        for issue in issues:
            print(f"{feed}: {issue}", file=sys.stderr)
        problems += len(issues)
    print(f"verified {len(ledgers)} ledgers, {corrections} corrections, {problems} problems")
    return 1 if problems else 0


def _crop(args: argparse.Namespace) -> int:
    feed = archive.feed_id(args.notice)
    notice = archive.load_ocr(feed)
    images = notice.get("images", [])
    if not 1 <= args.image <= len(images):
        raise CliError(f"image index out of range: {args.image} (notice has {len(images)})")

    image = images[args.image - 1]
    if image.get("error"):
        raise CliError(f"image {args.image} failed OCR: {image['error']}")
    path = image.get("path")
    if not path:
        raise CliError(f"image {args.image} has no local file")

    blocks = image.get("blocks", [])
    box = crop.crop_box(
        blocks, args.block, context=args.context, pad_x=args.pad_x, pad_y=args.pad_y
    )
    rendered = crop.render(
        archive.REPO_ROOT / path, box, scale=args.scale, target_width=args.target_width
    )

    out = Path(args.out) if args.out else CROP_DIR / feed / f"img{args.image:02d}-b{args.block:04d}.png"
    if not out.is_absolute():
        out = archive.REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(out, format="PNG", optimize=False)

    print(f"crop: {out.relative_to(archive.REPO_ROOT).as_posix()}")
    print(f"image: {path} ({rendered.width}x{rendered.height})")
    left, top, right, bottom = box
    for index, block in enumerate(blocks):
        block_left, block_top, block_right, block_bottom = crop.block_box(block)
        if block_top >= top and block_bottom <= bottom and block_right > left and block_left < right:
            mark = ">>" if index == args.block else "  "
            print(f"{mark} b{index:04d} {float(block.get('score', 0.0)):.4f} | {block.get('text', '')}")
    return 0


def _edition(args: argparse.Namespace) -> int:
    if args.check:
        problems = edition.check()
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"edition: {len(problems)} problems")
        return 1 if problems else 0
    written = edition.write()
    print(f"edition: {len(written)} files changed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.cli", description="Local tooling for the announcement archive."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="check every correction ledger against the raw OCR")
    verify.set_defaults(func=_verify)

    crop_cmd = sub.add_parser("crop", help="render the source image around one OCR block")
    crop_cmd.add_argument("notice", help="feed id, with or without the dashen. prefix")
    crop_cmd.add_argument("image", type=int, help="1-based image index within the notice")
    crop_cmd.add_argument("block", type=int, help="0-based block index within the image")
    crop_cmd.add_argument("--context", type=int, default=0, help="extra lines above and below")
    crop_cmd.add_argument("--pad-x", type=int, default=40)
    crop_cmd.add_argument("--pad-y", type=int, default=12)
    crop_cmd.add_argument("--scale", type=float, default=0.0, help="0 fits --target-width")
    crop_cmd.add_argument("--target-width", type=float, default=1400.0)
    crop_cmd.add_argument("--out", default=None)
    crop_cmd.set_defaults(func=_crop)

    edition_cmd = sub.add_parser("edition", help="render the readable text edition")
    edition_cmd.add_argument("--check", action="store_true", help="fail when the committed files are stale")
    edition_cmd.set_defaults(func=_edition)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CliError, FileNotFoundError, ValueError, edition.EditionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
