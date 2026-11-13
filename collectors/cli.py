"""Command-line entry point for the data-acquisition pipeline.

    python -m collectors.cli corpus [--max-pages N] [--out DIR] [--allow-shrink]
    python -m collectors.cli nga [--before YYYY-MM-DD] [--from-dir DIR]
                                 [--limit N] [--out DIR] [--allow-shrink]
    python -m collectors.cli ocr [--family update] [--limit N]

Both harvest commands are idempotent: rerunning them rewrites nothing when the
upstream data has not changed. A partial run (``--max-pages``, ``--before`` or
``--limit``) is unioned with the committed corpus, so it never truncates posts
it did not visit; ``--allow-shrink`` is required to replace the corpus with
exactly the rows fetched. An empty or malformed response exits non-zero without
touching the corpus.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import dashen
from . import nga
from . import ocr

_DASHEN_ERRORS = (dashen.DashenError, ValueError, OSError)
_NGA_ERRORS = (nga.NgaError, ValueError, OSError)


def _corpus(args: argparse.Namespace) -> int:
    try:
        summary = dashen.harvest_corpus(
            max_pages=args.max_pages,
            allow_shrink=args.allow_shrink,
            out=args.out,
        )
    except _DASHEN_ERRORS as exc:
        print(f"corpus: {exc}", file=sys.stderr)
        return 1
    print(
        f"corpus: {summary['post_count']} posts, {summary['page_count']} pages, "
        f"changed={summary['changed']}"
    )
    return 0


def _nga(args: argparse.Namespace) -> int:
    try:
        summary = nga.harvest(
            before=args.before,
            from_dir=args.from_dir,
            index_html=args.index_html,
            delay=args.delay,
            limit=args.limit,
            allow_shrink=args.allow_shrink,
            out=args.out,
        )
    except _NGA_ERRORS as exc:
        print(f"nga: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _ocr(args: argparse.Namespace) -> int:
    stats = ocr.run(family=args.family, limit=args.limit, force=args.force)
    print(json.dumps(stats, sort_keys=True))
    return 0 if stats["failed_posts"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hpma-announcements")
    sub = parser.add_subparsers(dest="command", required=True)

    corpus = sub.add_parser("corpus", help="harvest every post's text and metadata")
    corpus.add_argument("--source", default="dashen", choices=["dashen"])
    corpus.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="stop after N pages (smoke); existing posts are kept unless --allow-shrink",
    )
    corpus.add_argument(
        "--out",
        default=None,
        help="write posts.jsonl and manifest.json to this directory (default: corpus/dashen)",
    )
    corpus.add_argument(
        "--allow-shrink",
        action="store_true",
        help="replace the corpus with exactly this run's posts; required to drop rows",
    )
    corpus.set_defaults(func=_corpus)

    nga_cmd = sub.add_parser("nga", help="snapshot the NGA index and harvest its notices")
    nga_cmd.add_argument(
        "--before",
        default=None,
        help=(
            "only fetch production notices before this date (default: all); "
            "existing posts are kept unless --allow-shrink"
        ),
    )
    nga_cmd.add_argument(
        "--from-dir",
        default=None,
        help="read <tid>.html pages from this directory instead of the network",
    )
    nga_cmd.add_argument(
        "--index-html",
        default=None,
        help="read the index page from this file (with or without --from-dir)",
    )
    nga_cmd.add_argument("--delay", type=float, default=5.0, help="seconds between requests")
    nga_cmd.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process only the first N notices; existing posts are kept unless --allow-shrink",
    )
    nga_cmd.add_argument(
        "--out",
        default=None,
        help="write the corpus to this directory (default: corpus/nga)",
    )
    nga_cmd.add_argument(
        "--allow-shrink",
        action="store_true",
        help="replace the corpus with exactly this run's notices; required to drop rows",
    )
    nga_cmd.set_defaults(func=_nga)

    ocr_cmd = sub.add_parser("ocr", help="download images and OCR one label family")
    ocr_cmd.add_argument("--family", default="update")
    ocr_cmd.add_argument("--limit", type=int, default=None, help="process only the first N posts (smoke)")
    ocr_cmd.add_argument(
        "--force",
        action="store_true",
        help="re-OCR every notice under the current engine, overriding kept foreign records",
    )
    ocr_cmd.set_defaults(func=_ocr)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
