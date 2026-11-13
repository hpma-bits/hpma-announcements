"""Command-line entry point for the archive's local tooling.

    python -m tools.cli verify
    python -m tools.cli crop <notice> <image> <block> [--context N] [--scale F]
    python -m tools.cli edition [--check]
    python -m tools.cli audit-status [--check | --soft]
    python -m tools.cli doctor [--strict] [--require-ocr] [--require-audit] [--ocr-smoke]
    python -m tools.cli audit-ocr [--engine {v4,v6}] [--family update] [--limit N]
    python -m tools.cli apply-verdicts [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from collectors import ocr

from . import (
    apply_verdicts,
    archive,
    audit_compare,
    audit_status,
    crop,
    dataset,
    doctor,
    edition,
    ocr_audit,
)

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
    layer_dir = "derived/ocr-v6/dashen" if args.v6 else "derived/ocr/dashen"
    notice = archive.read_json(archive.REPO_ROOT / layer_dir / f"{feed}.json")
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


def _doctor(args: argparse.Namespace) -> int:
    return doctor.run(
        strict=args.strict,
        require_ocr=args.require_ocr,
        require_audit=args.require_audit,
        ocr_smoke=args.ocr_smoke,
    )


def _audit_ocr(args: argparse.Namespace) -> int:
    stats = ocr_audit.run(family=args.family, limit=args.limit, engine=args.engine)
    print(json.dumps(stats, sort_keys=True))
    return 0


def _audit_compare(args: argparse.Namespace) -> int:
    stats = audit_compare.run_compare(
        notice_ids=args.notice or None, crops=args.crops
    )
    print(json.dumps(stats, sort_keys=True))
    return 0


def _audit_status(args: argparse.Namespace) -> int:
    return audit_status.run(soft=args.soft, check=args.check)


def _apply_verdicts(args: argparse.Namespace) -> int:
    return apply_verdicts.run(dry_run=args.dry_run)


def _dataset(args: argparse.Namespace) -> int:
    return dataset.run(check=args.check, allow_unverified=args.allow_unverified)


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
    crop_cmd.add_argument(
        "--v6", action="store_true",
        help="address blocks in the v6 audit layer (derived/ocr-v6) instead of the v4 record",
    )
    crop_cmd.set_defaults(func=_crop)

    edition_cmd = sub.add_parser("edition", help="render the readable text edition")
    edition_cmd.add_argument("--check", action="store_true", help="fail when the committed files are stale")
    edition_cmd.set_defaults(func=_edition)

    doctor_cmd = sub.add_parser(
        "doctor", help="report whether this interpreter can run the archive tooling"
    )
    doctor_cmd.add_argument(
        "--strict", action="store_true", help="also fail on warnings (e.g. optional engines missing)"
    )
    doctor_cmd.add_argument(
        "--require-ocr", action="store_true", help="fail when the v4 OCR engine (rapidocr-onnxruntime) is not usable"
    )
    doctor_cmd.add_argument(
        "--require-audit", action="store_true", help="fail when the v6 audit engine (rapidocr) is not usable"
    )
    doctor_cmd.add_argument(
        "--ocr-smoke",
        action="store_true",
        help="run each installed engine on one committed image, offline",
    )
    doctor_cmd.set_defaults(func=_doctor)

    audit_cmd = sub.add_parser(
        "audit-ocr",
        help="OCR every notice image with the independent second reader (no downloads)",
    )
    audit_cmd.add_argument(
        "--engine",
        choices=sorted(ocr.ENGINE_BUILDERS),
        default="v6",
        help=(
            "second-reader engine (default: v6); choose v4 when the notice "
            "record itself was produced by v6"
        ),
    )
    audit_cmd.add_argument("--family", default="update")
    audit_cmd.add_argument("--limit", type=int, default=None, help="process only the first N posts")
    audit_cmd.set_defaults(func=_audit_ocr)

    compare_cmd = sub.add_parser(
        "audit-compare",
        help="align the v4 record against the v6 second reading and queue differences",
    )
    compare_cmd.add_argument(
        "notice", nargs="*", help="feed id(s) to compare (default: every notice with a v6 reading)"
    )
    compare_cmd.add_argument(
        "--crops", action="store_true", help="render an evidence crop per review item"
    )
    compare_cmd.set_defaults(func=_audit_compare)

    status_cmd = sub.add_parser(
        "audit-status",
        help="build the per-block audit ledger and fail while unreviewed blocks remain",
    )
    status_mode = status_cmd.add_mutually_exclusive_group()
    status_mode.add_argument(
        "--check",
        action="store_true",
        help="rebuild in memory and byte-compare with derived/audit; write nothing",
    )
    status_mode.add_argument(
        "--soft", action="store_true", help="report queued blocks without failing"
    )
    status_cmd.set_defaults(func=_audit_status)

    apply_cmd = sub.add_parser(
        "apply-verdicts",
        help="apply adjudication verdicts from audit/ to the correction ledger",
    )
    apply_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report every verdict without writing a ledger",
    )
    apply_cmd.set_defaults(func=_apply_verdicts)

    dataset_cmd = sub.add_parser(
        "dataset", help="build dataset/notices.jsonl, the website-facing data row set"
    )
    dataset_cmd.add_argument("--check", action="store_true", help="fail when the committed file is stale")
    dataset_cmd.add_argument(
        "--allow-unverified",
        action="store_true",
        help="publish Dashen rows whose audit quality is missing or not verified",
    )
    dataset_cmd.set_defaults(func=_dataset)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CliError, FileNotFoundError, ValueError, edition.EditionError,
            audit_status.MissingEvidenceError, dataset.DatasetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
