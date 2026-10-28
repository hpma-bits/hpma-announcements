"""Command-line entry point: ``python -m collectors.cli <index|validate>``."""

from __future__ import annotations

import argparse
import json
import sys

from . import dashen
from . import ocr
from .store import Store


def _store(args: argparse.Namespace) -> Store:
    return Store(args.root) if args.root else Store()


def _index(args: argparse.Namespace) -> int:
    path = _store(args).write_index()
    print(f"wrote {path}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    errors = _store(args).validate()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("validation ok")
    return 0


def _corpus(args: argparse.Namespace) -> int:
    summary = dashen.harvest_corpus(args.root, max_pages=args.max_pages)
    print(
        f"corpus: {summary['post_count']} posts, {summary['page_count']} pages, "
        f"changed={summary['changed']}"
    )
    return 0


def _ocr(args: argparse.Namespace) -> int:
    stats = ocr.run(args.root, family=args.family, limit=args.limit)
    print(json.dumps(stats, sort_keys=True))
    return 0 if stats["failed_posts"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hpma-announcements")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="rebuild index.json")
    index.add_argument("--root", help="repository root (tests)")
    index.set_defaults(func=_index)

    validate = sub.add_parser("validate", help="validate data/*.json")
    validate.add_argument("--root", help="repository root (tests)")
    validate.set_defaults(func=_validate)

    corpus = sub.add_parser("corpus", help="harvest the Dashen post corpus (text only)")
    corpus.add_argument("--source", default="dashen", choices=["dashen"])
    corpus.add_argument("--max-pages", type=int, default=None, help="stop after N pages (smoke)")
    corpus.add_argument("--root", help="repository root (tests)")
    corpus.set_defaults(func=_corpus)

    ocr_cmd = sub.add_parser("ocr", help="OCR notice images for a label family")
    ocr_cmd.add_argument("--family", default="update")
    ocr_cmd.add_argument("--limit", type=int, default=None, help="process only the first N posts (smoke)")
    ocr_cmd.add_argument("--root", help="repository root (tests)")
    ocr_cmd.set_defaults(func=_ocr)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
