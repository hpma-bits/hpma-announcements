"""Command-line entry point for the data-acquisition pipeline.

    python -m collectors.cli corpus [--max-pages N]
    python -m collectors.cli ocr [--family update] [--limit N]

Both commands are idempotent: rerunning them rewrites nothing when the upstream
data has not changed.
"""

from __future__ import annotations

import argparse
import json

from . import dashen
from . import ocr


def _corpus(args: argparse.Namespace) -> int:
    summary = dashen.harvest_corpus(max_pages=args.max_pages)
    print(
        f"corpus: {summary['post_count']} posts, {summary['page_count']} pages, "
        f"changed={summary['changed']}"
    )
    return 0


def _ocr(args: argparse.Namespace) -> int:
    stats = ocr.run(family=args.family, limit=args.limit)
    print(json.dumps(stats, sort_keys=True))
    return 0 if stats["failed_posts"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hpma-announcements")
    sub = parser.add_subparsers(dest="command", required=True)

    corpus = sub.add_parser("corpus", help="harvest every post's text and metadata")
    corpus.add_argument("--source", default="dashen", choices=["dashen"])
    corpus.add_argument("--max-pages", type=int, default=None, help="stop after N pages (smoke)")
    corpus.set_defaults(func=_corpus)

    ocr_cmd = sub.add_parser("ocr", help="download images and OCR one label family")
    ocr_cmd.add_argument("--family", default="update")
    ocr_cmd.add_argument("--limit", type=int, default=None, help="process only the first N posts (smoke)")
    ocr_cmd.set_defaults(func=_ocr)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
