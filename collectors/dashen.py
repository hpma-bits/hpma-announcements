"""Dashen corpus harvester for the official HPMA account.

Collects every post's text and metadata — no image bytes — into
``corpus/dashen/posts.jsonl``. The corpus is the evidence base for the
selection study; notice records and images come later, from the induced
selection procedure.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import http
from .paths import REPO_ROOT

UID = "29c10cd0344f462e82e16a516001f0dd"
FEED_TYPES = "1,2,3,4,6,7,10,11"
LIST_URL = "https://inf.ds.163.com/v1/web/feed/basic/getSomeOneFeeds"
TZ = timezone(timedelta(hours=8))

Fetcher = Callable[[str], dict[str, Any]]


def _iso(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=TZ).replace(microsecond=0).isoformat()


def extract_title(body: dict[str, Any]) -> str | None:
    """Type 2 posts carry their title as the first body-text line; type 6
    articles carry real ``title``/``subTitle`` fields. One chain covers both."""
    for key in ("title", "subTitle"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    first_line = (body.get("text") or "").split("\n", 1)[0].strip()
    return first_line or None


def _feed_url(post_id: str, post_type: int | None) -> str:
    if post_type == 6:
        return f"https://ds.163.com/article/{post_id}/"
    return f"https://ds.163.com/feed/{post_id}/"


def parse_post(feed: dict[str, Any]) -> dict[str, Any]:
    try:
        content = json.loads(feed.get("content") or "{}")
    except json.JSONDecodeError:
        print(f"[dashen] unparseable content for feed {feed.get('id')}", file=sys.stderr)
        content = {}
    body = content.get("body") or {}
    media = body.get("media") or []
    image_urls = [
        item["url"]
        for item in media
        if isinstance(item, dict)
        and str(item.get("mimeType") or "").startswith("image/")
        and item.get("url")
    ]
    post_type = feed.get("type")
    return {
        "id": feed["id"],
        "type": post_type,
        "published_at": _iso(feed.get("createTime")),
        "source_updated_at": _iso(feed.get("updateTime")),
        "title": extract_title(body),
        "text": body.get("text") or None,
        "long_text": body.get("longText") or None,
        "image_count": len(image_urls),
        "image_urls": image_urls,
        "topics": [
            topic["topicName"]
            for topic in (feed.get("topicInfoList") or [])
            if topic.get("topicName")
        ],
        "deleted": bool(feed.get("deleteTime")),
        "feed_url": _feed_url(feed["id"], post_type),
    }


def parse_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    result = payload.get("result") or {}
    feeds = result.get("feeds") or []
    next_max_time = (result.get("nextRangeParam") or {}).get("maxTime")
    return [parse_post(feed) for feed in feeds], next_max_time


def _page_url(uid: str, max_time: int | None) -> str:
    url = f"{LIST_URL}?someOneUid={uid}&feedTypes={FEED_TYPES}"
    if max_time is not None:
        url += f"&maxTime={max_time}"
    return url


def harvest_corpus(
    root: str | Path | None = None,
    *,
    uid: str = UID,
    max_pages: int | None = None,
    fetch: Fetcher | None = None,
) -> dict[str, Any]:
    """Walk the account's feed and write ``corpus/dashen/posts.jsonl``.

    Rerunning on an unchanged feed is byte-identical and leaves the manifest
    (and its ``fetched_at``) untouched.
    """
    fetch = fetch or http.fetch_json
    seen: dict[str, dict[str, Any]] = {}
    cursor: int | None = None
    cursors: set[int] = set()
    pages = 0
    while max_pages is None or pages < max_pages:
        records, next_max_time = parse_page(fetch(_page_url(uid, cursor)))
        pages += 1
        new_ids = 0
        for record in records:
            if record["id"] not in seen:
                seen[record["id"]] = record
                new_ids += 1
        if not records or new_ids == 0:
            break
        if next_max_time is None or next_max_time in cursors:
            break
        cursors.add(next_max_time)
        cursor = next_max_time

    posts = sorted(seen.values(), key=lambda post: (post["published_at"] or "", post["id"]))
    corpus_dir = (Path(root) if root else REPO_ROOT) / "corpus" / "dashen"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    posts_path = corpus_dir / "posts.jsonl"
    text = "".join(
        json.dumps(post, ensure_ascii=False, separators=(",", ":")) + "\n" for post in posts
    )
    changed = not posts_path.exists() or posts_path.read_text(encoding="utf-8") != text
    if changed:
        posts_path.write_text(text, encoding="utf-8", newline="\n")
        manifest = {
            "uid": uid,
            "fetched_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "page_count": pages,
            "post_count": len(posts),
            "feed_types": FEED_TYPES,
            "notes": "text-only corpus; image bytes are not downloaded",
        }
        (corpus_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return {
        "post_count": len(posts),
        "page_count": pages,
        "changed": changed,
        "path": str(posts_path),
    }
