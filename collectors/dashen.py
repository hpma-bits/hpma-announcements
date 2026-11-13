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

from . import atomic, http
from .paths import REPO_ROOT

UID = "29c10cd0344f462e82e16a516001f0dd"
FEED_TYPES = "1,2,3,4,6,7,10,11"
LIST_URL = "https://inf.ds.163.com/v1/web/feed/basic/getSomeOneFeeds"
TZ = timezone(timedelta(hours=8))

Fetcher = Callable[[str], dict[str, Any]]


class DashenError(RuntimeError):
    """The feed cannot be trusted; refuse to overwrite the corpus."""


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
    raw_content = feed.get("content")
    if isinstance(raw_content, dict):
        content = raw_content
    else:
        try:
            content = json.loads(raw_content or "{}")
        except (json.JSONDecodeError, TypeError):
            print(f"[dashen] unparseable content for feed {feed.get('id')}", file=sys.stderr)
            content = {}
    if not isinstance(content, dict):
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
    """Parse one feed page, rejecting anything that is not the expected shape.

    An empty body, an error payload or a missing ``feeds`` list must raise
    before the caller can touch ``posts.jsonl``: a truncated corpus is worse
    than a failed harvest.
    """
    if not isinstance(payload, dict):
        raise DashenError("feed response is not a JSON object; refusing to write the corpus")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise DashenError(
            "feed response has no result object (empty or malformed body); "
            "refusing to write the corpus"
        )
    feeds = result.get("feeds")
    if not isinstance(feeds, list):
        raise DashenError(
            "feed response has no feeds list (empty or malformed body); "
            "refusing to write the corpus"
        )
    records: list[dict[str, Any]] = []
    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("id"):
            raise DashenError(
                "feed response contains an entry without an id (malformed body); "
                "refusing to write the corpus"
            )
        records.append(parse_post(feed))
    range_param = result.get("nextRangeParam") or {}
    next_max_time = range_param.get("maxTime") if isinstance(range_param, dict) else None
    return records, next_max_time


def _page_url(uid: str, max_time: int | None) -> str:
    url = f"{LIST_URL}?someOneUid={uid}&feedTypes={FEED_TYPES}"
    if max_time is not None:
        url += f"&maxTime={max_time}"
    return url


def _read_previous_posts(path: Path) -> dict[str, dict[str, Any]]:
    """Load the committed corpus by id; refuse to overwrite an unreadable one."""
    if not path.exists():
        return {}
    posts: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            post = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DashenError(
                f"existing corpus {path} line {number} is not valid JSON: {exc}; "
                "fix or remove it before harvesting"
            ) from exc
        if not isinstance(post, dict) or not post.get("id"):
            raise DashenError(
                f"existing corpus {path} line {number} has no post id; "
                "fix or remove it before harvesting"
            )
        posts[post["id"]] = post
    return posts


def _manifest(uid: str, pages: int, post_count: int) -> str:
    payload = {
        "uid": uid,
        "fetched_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "page_count": pages,
        "post_count": post_count,
        "feed_types": FEED_TYPES,
        "notes": "text-only corpus; image bytes are not downloaded",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def harvest_corpus(
    root: str | Path | None = None,
    *,
    uid: str = UID,
    max_pages: int | None = None,
    fetch: Fetcher | None = None,
    allow_shrink: bool = False,
    out: str | Path | None = None,
) -> dict[str, Any]:
    """Walk the account's feed and write ``corpus/dashen/posts.jsonl``.

    A partial run (``max_pages``) is unioned with the committed corpus by id,
    so it never truncates posts it did not visit. ``allow_shrink`` opts into
    replacing the corpus with exactly this run's set; without it a smaller
    result cannot be written. An empty or malformed page raises
    :class:`DashenError` before any file is touched.

    ``out`` overrides the corpus directory (default ``<root>/corpus/dashen``)
    so smoke runs can write somewhere else. Rerunning on an unchanged feed is
    byte-identical and leaves the manifest (and its ``fetched_at``) untouched.
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

    if not seen:
        raise DashenError(
            "feed returned no posts (empty feeds or malformed body); "
            "refusing to write an empty corpus"
        )

    corpus_dir = Path(out) if out else (Path(root) if root else REPO_ROOT) / "corpus" / "dashen"
    posts_path = corpus_dir / "posts.jsonl"
    previous = _read_previous_posts(posts_path)

    if allow_shrink:
        merged = dict(seen)
    else:
        # Keep every committed post the partial feed did not visit; fresh
        # records win for the ids this run did see.
        merged = dict(previous)
        merged.update(seen)
    if not merged:
        raise DashenError("harvest produced no posts; refusing to write an empty corpus")

    posts = sorted(merged.values(), key=lambda post: (post["published_at"] or "", post["id"]))
    text = "".join(
        json.dumps(post, ensure_ascii=False, separators=(",", ":")) + "\n" for post in posts
    )
    changed = not posts_path.exists() or posts_path.read_text(encoding="utf-8") != text
    manifest_path = corpus_dir / "manifest.json"
    if changed:
        atomic.write_text(posts_path, text)
        atomic.write_text(manifest_path, _manifest(uid, pages, len(posts)))
    elif not manifest_path.exists():
        atomic.write_text(manifest_path, _manifest(uid, pages, len(posts)))

    dropped = sorted(set(previous) - set(merged))
    return {
        "post_count": len(posts),
        "page_count": pages,
        "changed": changed,
        "dropped": dropped,
        "path": str(posts_path),
    }
