"""Atomic text writes for the collectors.

A collector must never leave a half-written ``posts.jsonl`` behind: the corpus
is the evidence base, so an interrupted run keeps the previous file intact.
Writes go to a temporary file in the destination directory and are moved into
place with :func:`os.replace`, which is atomic on POSIX and Windows.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text(path: str | Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, creating parent directories."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_bytes(path: str | Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, creating parent directories.

    Downloaded image bytes are evidence like the corpus, so an interrupted
    run must never leave a truncated asset behind.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
