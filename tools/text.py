"""Text rules shared by every projection.

The platform watermark and body normalization live here, not in the Markdown
renderer, so the edition, dataset and audit read exactly the same text rules.
"""

from __future__ import annotations

import re

WATERMARK = "大神@哈利波特魔法觉醒"
# The stamped watermark reaches the text layer as line-end suffixes in several
# OCR spellings: the 大神 prefix or @ may be dropped or merged into content
# characters, and 特 may come back as 属. Anchored at the line end; a legitimate
# game-title mention always carries 《》 or a full-width colon before 觉醒.
WATERMARK_SUFFIX = re.compile(r"(?:大神|神)?\s*[＠@]?\s*哈利波[特属]?\s*魔?法?\s*觉醒\s*$")


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
    text = WATERMARK_SUFFIX.sub("", text).strip()
    return "" if text in WATERMARK else text
