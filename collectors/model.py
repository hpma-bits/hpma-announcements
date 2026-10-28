"""Record types for the update-notice archive."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Image:
    """One image attached to a notice.

    ``path`` / ``sha256`` / ``bytes`` / ``width`` / ``height`` stay ``None``
    until the image is actually downloaded and archived.
    """

    index: int
    url: str
    path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "url": self.url,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class Notice:
    """A normalized update notice.

    The store fills ``first_seen_at``, ``revision`` and ``content_hash``;
    collectors only fill the source-derived fields.
    """

    id: str
    url: str
    published_at: str
    source_updated_at: str
    notice_date: str
    title: str
    text: str | None = None
    images: list[Image] = field(default_factory=list)
    first_seen_at: str | None = None
    revision: int = 1
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "published_at": self.published_at,
            "source_updated_at": self.source_updated_at,
            "notice_date": self.notice_date,
            "title": self.title,
            "text": self.text,
            "images": [image.to_dict() for image in self.images],
            "first_seen_at": self.first_seen_at,
            "revision": self.revision,
            "content_hash": self.content_hash,
        }
