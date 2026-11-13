"""The readable edition: one Markdown file per notice plus a machine index.

An edition is a projection of primary data, not a new source of truth: it is
rebuilt from ``posts`` + ``ocr`` + ``corrections`` and must stay byte-identical
when nothing upstream changed (``python -m tools.cli edition --check``).

Two sources share one pipeline. Dashen notices carry long images, so their text
comes from OCR plus the correction ledger. NGA notices are already text, so
their edition has only ``## 正文``; no OCR result or ledger is expected. The
merged index marks one canonical reading per notice date: NGA when present,
otherwise Dashen.

The platform stamps every long Dashen image with a watermark. OCR reads it as
its own blocks, so it is removed by the shared text rules: a block that is only
watermark is dropped, and a watermark suffix merged into a content line is cut
off.

The ledger can also drop a block outright (``drop: true``) when OCR emitted a
duplicate slice or other artifact that has no line of its own in the image.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import archive, projection
from .text import clean_block, normalize_body

EDITION_DIR = Path("edition/dashen")
INDEX_PATH = Path("edition/notices.jsonl")

Record = dict[str, Any]

__all__ = [
    "EditionError",
    "build",
    "check",
    "clean_block",
    "normalize_body",
    "render_notice",
    "write",
]


class EditionError(RuntimeError):
    """The data cannot produce a correct edition (stale ledger, missing OCR)."""


def _scalar(value: str) -> str:
    """YAML scalar for one line: double-quoted, so no character needs escaping rules."""
    return json.dumps(value, ensure_ascii=False)


def _images(notice: Record | None) -> list[str]:
    if not notice:
        return []
    return [image["path"] for image in notice.get("images", []) if image.get("path")]


def _frontmatter(
    post: Record, selection: Record, notice: Record | None, source: str
) -> list[str]:
    lines = ["---"]
    fields = [
        ("id", archive.notice_id(source, post["id"])),
        ("source", source),
        ("title", post["title"]),
        ("published_at", post["published_at"]),
        ("notice_date", selection["notice_date"]),
        ("url", archive.post_url(post)),
    ]
    if post.get("author_uid"):
        fields.append(("source_author_uid", post["author_uid"]))
    for key, value in fields:
        lines.append(f"{key}: {_scalar(value)}")
    images = _images(notice)
    if images:
        lines.append("images:")
        lines.extend(f"  - {_scalar(path)}" for path in images)
    else:
        lines.append("images: []")
    lines.append("---")
    return lines


def index_record(
    post: Record, selection: Record, notice: Record | None, source: str
) -> Record:
    record = {
        "id": archive.notice_id(source, post["id"]),
        "source": source,
        "title": post["title"],
        "published_at": post["published_at"],
        "notice_date": selection["notice_date"],
        "url": archive.post_url(post),
        "images": _images(notice),
    }
    if post.get("author_uid"):
        record["source_author_uid"] = post["author_uid"]
    return record


def render_notice(
    post: Record,
    selection: Record,
    notice: Record | None = None,
    ledger: Record | None = None,
    source: str = archive.DEFAULT_SOURCE,
) -> str:
    """One notice as Markdown: front matter, body text, text per image.

    ``notice`` is the OCR result; text-only sources pass ``None`` and get no
    ``## 图片文字`` section.
    """
    lines = _frontmatter(post, selection, notice, source)

    body = normalize_body(post.get("text") or "")
    if body:
        lines += ["", "## 正文", "", body]

    sections = projection.effective_sections(notice, ledger) if notice else []
    if sections:
        lines += ["", "## 图片文字"]
        for index, texts in sections:
            lines += ["", f"### 图 {index}", "", *texts]

    return "\n".join(lines).rstrip() + "\n"


def _source_present(source: archive.Source, base: Path) -> bool:
    corpus = base / source.corpus
    selection = base / source.selection
    if corpus.exists() and selection.exists():
        return True
    if corpus.exists() != selection.exists():
        raise EditionError(
            f"{source.name}: corpus and selection must both exist "
            f"({source.corpus}, {source.selection})"
        )
    return False


def build(root: Path | None = None) -> dict[Path, str]:
    """Every edition file, keyed by path relative to ``root``."""
    base = Path(root) if root else archive.REPO_ROOT
    outputs: dict[Path, str] = {}
    index_rows: list[Record] = []

    for source in archive.SOURCES.values():
        if not _source_present(source, base):
            continue
        for post, selection in archive.selected_posts(root, "update", source.name):
            feed = post["id"]
            notice = archive.load_ocr(feed, root) if source.ocr else None
            ledger = archive.load_ledger(feed, root) if source.ocr else None
            try:
                outputs[source.edition_dir / f"{feed}.md"] = render_notice(
                    post, selection, notice, ledger, source.name
                )
            except ValueError as exc:
                raise EditionError(f"{feed}: {exc}") from exc
            index_rows.append(index_record(post, selection, notice, source.name))

    index_rows.sort(key=lambda row: (row["published_at"], row["id"]))
    projection.assign_canonical(index_rows)
    index_lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in index_rows]
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
    for source in archive.SOURCES.values():
        directory = base / source.edition_dir
        for path in sorted(directory.glob("*.md")):
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
