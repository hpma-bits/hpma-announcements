"""The readable edition: one Markdown file per notice plus a machine index.

An edition is a projection of primary data, not a new source of truth: it is
rebuilt from ``posts`` + ``ocr`` + ``corrections`` and must stay byte-identical
when nothing upstream changed (``python -m tools.cli edition --check``).

The platform stamps every long image with a watermark. OCR reads it as its own
blocks, so it is removed here: a block that is only watermark is dropped, and a
watermark suffix merged into a content line is cut off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import archive

EDITION_DIR = Path("edition/dashen")
INDEX_PATH = Path("edition/notices.jsonl")

WATERMARK = "大神@哈利波特魔法觉醒"

Record = dict[str, Any]


class EditionError(RuntimeError):
    """The data cannot produce a correct edition (stale ledger, missing OCR)."""


def normalize_body(text: str) -> str:
    """Line endings to ``\\n``, trailing spaces removed, outer blank lines trimmed."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def clean_block(text: str) -> str:
    """OCR block text with the platform watermark removed; empty when it was only that."""
    text = text.replace(WATERMARK, "").strip()
    return "" if text in WATERMARK else text


def _scalar(value: str) -> str:
    """YAML scalar for one line: double-quoted, so no character needs escaping rules."""
    return json.dumps(value, ensure_ascii=False)


def _frontmatter(post: Record, selection: Record, notice: Record) -> list[str]:
    images = [image["path"] for image in notice.get("images", []) if image.get("path")]
    lines = ["---"]
    for key, value in (
        ("id", f"dashen.{post['id']}"),
        ("title", post["title"]),
        ("published_at", post["published_at"]),
        ("notice_date", selection["notice_date"]),
        ("url", post["feed_url"]),
    ):
        lines.append(f"{key}: {_scalar(value)}")
    lines.append("images:")
    lines.extend(f"  - {_scalar(path)}" for path in images)
    lines.append("---")
    return lines


def index_record(post: Record, selection: Record, notice: Record) -> Record:
    return {
        "id": f"dashen.{post['id']}",
        "title": post["title"],
        "published_at": post["published_at"],
        "notice_date": selection["notice_date"],
        "url": post["feed_url"],
        "images": [image["path"] for image in notice.get("images", []) if image.get("path")],
    }


def render_notice(
    post: Record,
    selection: Record,
    notice: Record,
    ledger: Record | None = None,
) -> str:
    """One notice as Markdown: front matter, body text, text per image."""
    overrides = archive.corrected_blocks(notice, ledger)
    lines = _frontmatter(post, selection, notice)

    body = normalize_body(post.get("text") or "")
    if body:
        lines += ["", "## 正文", "", body]

    sections: list[tuple[int, list[str]]] = []
    for image in notice.get("images", []):
        index = image["index"]
        texts: list[str] = []
        previous = ""
        for block_index, block in enumerate(image.get("blocks", [])):
            text = overrides.get((index, block_index), block.get("text") or "")
            cleaned = clean_block(text)
            if not cleaned or cleaned == previous:
                # Overlapping OCR tiles capture the same line twice; keep one.
                continue
            texts.append(cleaned)
            previous = cleaned
        if texts:
            sections.append((index, texts))

    if sections:
        lines += ["", "## 图片文字"]
        for index, texts in sections:
            lines += ["", f"### 图 {index}", "", *texts]

    return "\n".join(lines).rstrip() + "\n"


def build(root: Path | None = None) -> dict[Path, str]:
    """Every edition file, keyed by path relative to ``root``."""
    outputs: dict[Path, str] = {}
    index_lines: list[str] = []
    for post, selection in archive.selected_posts(root):
        feed = post["id"]
        notice = archive.load_ocr(feed, root)
        ledger = archive.load_ledger(feed, root)
        try:
            outputs[EDITION_DIR / f"{feed}.md"] = render_notice(post, selection, notice, ledger)
        except ValueError as exc:
            raise EditionError(f"{feed}: {exc}") from exc
        index_lines.append(
            json.dumps(index_record(post, selection, notice), ensure_ascii=False, sort_keys=True)
        )
    outputs[INDEX_PATH] = "\n".join(index_lines) + "\n"
    return outputs


def check(root: Path | None = None) -> list[str]:
    """Problems that mean the committed edition is not what the data produces."""
    base = Path(root) if root else archive.REPO_ROOT
    outputs = build(root)
    problems: list[str] = []
    for relative, content in outputs.items():
        path = base / relative
        if not path.exists():
            problems.append(f"missing {relative.as_posix()}")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"stale {relative.as_posix()}")
    for path in sorted((base / EDITION_DIR).glob("*.md")):
        relative = path.relative_to(base)
        if relative not in outputs:
            problems.append(f"orphan {relative.as_posix()}")
    return problems


def write(root: Path | None = None, outputs: dict[Path, str] | None = None) -> list[Path]:
    """Write files whose bytes differ; return the paths that changed."""
    base = Path(root) if root else archive.REPO_ROOT
    outputs = build(root) if outputs is None else outputs
    written: list[Path] = []
    for relative, content in sorted(outputs.items()):
        path = base / relative
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(relative)
    return written
