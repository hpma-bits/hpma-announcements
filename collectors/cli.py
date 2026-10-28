"""Command-line entry point: ``python -m collectors.cli <index|validate>``."""

from __future__ import annotations

import argparse
import sys

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hpma-announcements")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="rebuild index.json")
    index.add_argument("--root", help="repository root (tests)")
    index.set_defaults(func=_index)

    validate = sub.add_parser("validate", help="validate data/*.json")
    validate.add_argument("--root", help="repository root (tests)")
    validate.set_defaults(func=_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
