"""Read access to the archive's data and its correction ledger.

Primary data — the archive itself, never regenerated from anything else:

    corpus/dashen/posts.jsonl               every post, one JSON object per line
    study/labels.jsonl                      which posts are notices, and their date
    assets/dashen/<feed_id>/<NN>.<ext>      original image bytes
    derived/ocr/corrections/dashen/<id>.json   human decisions about OCR text

Derived data — rebuildable, committed only so the archive reads without network:

    derived/ocr/dashen/<feed_id>.json       immutable OCR engine output

A correction locates one OCR block by ``(image_index, block_index)`` and keeps
the raw ``original`` beside the ``corrected`` reading. An entry whose
``original`` no longer matches the OCR block is stale: callers must refuse it
rather than apply it, so a changed OCR file can never silently rewrite text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]

CORPUS_PATH = Path("corpus/dashen/posts.jsonl")
SELECTION_PATH = Path("study/labels.jsonl")
OCR_DIR = Path("derived/ocr/dashen")
LEDGER_DIR = Path("derived/ocr/corrections/dashen")

Record = dict[str, Any]
Locator = tuple[int, int]


def feed_id(value: str) -> str:
    """Normalise ``dashen.<id>`` / ``<id>`` to the bare feed id used in paths."""
    return value.removeprefix("dashen.")


def read_json(path: Path) -> Record:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_jsonl(path: Path) -> Iterator[Record]:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def corpus_path(root: Path | None = None) -> Path:
    return (Path(root) if root else REPO_ROOT) / CORPUS_PATH


def selection_path(root: Path | None = None) -> Path:
    return (Path(root) if root else REPO_ROOT) / SELECTION_PATH


def load_posts(root: Path | None = None) -> list[Record]:
    return list(iter_jsonl(corpus_path(root)))


def load_selection(root: Path | None = None) -> dict[str, Record]:
    return {row["id"]: row for row in iter_jsonl(selection_path(root))}


def selected_posts(
    root: Path | None = None, family: str = "update"
) -> list[tuple[Record, Record]]:
    """Posts labelled ``family``, paired with their selection row, oldest first."""
    selection = load_selection(root)
    chosen = [
        (post, selection[post["id"]])
        for post in load_posts(root)
        if selection.get(post["id"], {}).get("label") == family
    ]
    chosen.sort(key=lambda pair: (pair[0]["published_at"], pair[0]["id"]))
    return chosen


def ocr_path(feed: str, root: Path | None = None) -> Path:
    base = (Path(root) if root else REPO_ROOT) / OCR_DIR
    return base / f"{feed_id(feed)}.json"


def load_ocr(feed: str, root: Path | None = None) -> Record:
    path = ocr_path(feed, root)
    if not path.exists():
        raise FileNotFoundError(f"no OCR result for {feed_id(feed)}: {path}")
    return read_json(path)


def iter_ocr(root: Path | None = None) -> Iterator[tuple[str, Record]]:
    base = (Path(root) if root else REPO_ROOT) / OCR_DIR
    for path in sorted(base.glob("*.json")):
        yield path.stem, read_json(path)


def ledger_path(feed: str, root: Path | None = None) -> Path:
    base = (Path(root) if root else REPO_ROOT) / LEDGER_DIR
    return base / f"{feed_id(feed)}.json"


def load_ledger(feed: str, root: Path | None = None) -> Record | None:
    path = ledger_path(feed, root)
    return read_json(path) if path.exists() else None


def iter_ledgers(root: Path | None = None) -> Iterator[tuple[str, Record]]:
    base = (Path(root) if root else REPO_ROOT) / LEDGER_DIR
    for path in sorted(base.glob("*.json")):
        yield path.stem, read_json(path)


def check_ledger(notice: Record, ledger: Record) -> list[str]:
    """Return every problem that makes ``ledger`` unusable against ``notice``."""
    corrections = ledger.get("corrections")
    if not isinstance(corrections, list):
        return ["corrections must be a list"]

    problems: list[str] = []
    images = notice.get("images", [])
    seen: set[Locator] = set()

    for position, correction in enumerate(corrections):
        label = f"correction[{position}]"
        image_index = correction.get("image_index")
        block_index = correction.get("block_index")
        if not isinstance(image_index, int) or not isinstance(block_index, int):
            problems.append(f"{label}: image_index/block_index must be integers")
            continue

        locator = (image_index, block_index)
        if locator in seen:
            problems.append(f"{label}: duplicate locator {locator}")
        seen.add(locator)

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


def corrected_blocks(notice: Record, ledger: Record | None) -> dict[Locator, str]:
    """Map ``(image_index, block_index)`` to the text the source image shows.

    Raises :class:`ValueError` when the ledger is stale, so callers fail loudly
    instead of publishing text that the OCR output no longer supports.
    """
    if not ledger:
        return {}
    problems = check_ledger(notice, ledger)
    if problems:
        raise ValueError("; ".join(problems))
    return {
        (correction["image_index"], correction["block_index"]): correction["corrected"]
        for correction in ledger["corrections"]
    }
