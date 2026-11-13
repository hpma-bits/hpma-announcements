"""NGA supplement harvester for the update notices.

The verified Dashen account only starts publishing full update notices in
January 2022. The NGA community index thread ``tid=27915340`` links one thread
per weekly maintenance; those threads are plain-text reposts of the official
notice, so they need no OCR and are kept as a second source.

All production-server entries are harvested: the 18 before Dashen's first
notice fill the 2021 gap, and the 40 for 2022 provide the operator's plain text
for weeks that Dashen otherwise has only as OCR. Test-server entries are
snapshotted in the index but never fetched.

Access is public guest access (the page's own ``guestJs`` cookie), one request
every ``delay`` seconds, no login and no image mirroring. ``--from-dir`` parses
pages saved earlier, so a blocked or changed NGA can still be replayed offline.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import atomic, http
from .paths import REPO_ROOT

INDEX_TID = "27915340"
BASE_URL = "https://bbs.nga.cn"
TZ = timezone(timedelta(hours=8))

Fetcher = Callable[[str], bytes]

POSTCONTENT = re.compile(r"<p id='postcontent0'[^>]*>(.*?)</p>", re.S)
POSTSUBJECT = re.compile(r"<h3 id='postsubject0'>(.*?)</h3>", re.S)
POSTDATE = re.compile(r"<span id='postdate0'[^>]*>(.*?)</span>", re.S)
POSTERINFO = re.compile(r"<span id='posterinfo0'.*?</span>\s*</td>", re.S)
UID = re.compile(r"uid=(\d+)")
MONTHS = re.compile(r"===(\d{4})\.(\d{1,2})===")
LINK = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.S)
TID = re.compile(r"tid=(\d+)")
LABEL_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})")
IMG = re.compile(r"\[img\](.*?)\[/img\]", re.S | re.I)
COLLAPSE = re.compile(r"\[collapse([^\]]*)\](.*?)\[/collapse\]", re.S | re.I)
DELETED = re.compile(r"\[del\].*?\[/del\]", re.S | re.I)
QUOTE_OPEN = re.compile(r"\[quote\]", re.I)
QUOTE_CLOSE = re.compile(r"\[/quote\]", re.I)
EMOTICON = re.compile(r"\[s:[^\]]+\]", re.I)
OFFICIAL_BULLET = "[*]"
KNOWN_TAGS = re.compile(
    r"\[/?(?:color|b|i|u|s|del|ins|size|font|align|quote|list|collapse|table|tr|td|h\d?|"
    r"center|left|right|indent|pid|tid|uid)(?:=[^\]]*)?\]",
    re.I,
)
UPDATE_MARKERS = ("重要更新", "体验优化", "问题修正", "问题修复", "平衡性调整")
DATE_IN_TEXT = re.compile(r"(\d{1,2})月(\d{1,2})日")


class NgaError(RuntimeError):
    """The NGA data cannot be parsed or is not the expected notice."""


class NgaBlocked(NgaError):
    """NGA refused guest access; the caller should use ``--from-dir``."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode(data: bytes) -> str:
    return data.decode("gb18030", errors="replace")


def _text(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _post_fragment(page: str) -> str:
    match = POSTCONTENT.search(page)
    if not match:
        raise NgaError("page has no first-post content")
    return match.group(1)


def _entry_date(label: str, year: int, month: int) -> str | None:
    match = LABEL_DATE.match(label)
    if not match:
        return None
    day = int(match.group(2))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_index(page: str) -> list[dict[str, Any]]:
    """Every indexed thread, in the order the index lists it.

    An entry is one ``[url=/read.php?tid=...]`` line under a ``===YYYY.M===``
    heading. The same tid can appear twice (a thread covering both servers), so
    entries are not deduplicated here.
    """
    content = _post_fragment(page)
    parts = MONTHS.split(content)
    entries: list[dict[str, Any]] = []
    for position in range(1, len(parts), 3):
        year, month, chunk = int(parts[position]), int(parts[position + 1]), parts[position + 2]
        for url, raw_label in LINK.findall(chunk):
            tid_match = TID.search(url)
            if not tid_match:
                continue
            label = _text(raw_label)
            if "维护详情" not in label:
                continue
            if "正式服" in label:
                kind = "production"
            elif "体验服" in label:
                kind = "test"
            else:
                kind = "other"
            entries.append(
                {
                    "month": f"{year}.{month}",
                    "year": year,
                    "month_number": month,
                    "label": label,
                    "tid": tid_match.group(1),
                    "kind": kind,
                    "notice_date": _entry_date(label, year, month),
                    "thread_url": f"{BASE_URL}/read.php?tid={tid_match.group(1)}",
                }
            )
    return entries


def top_level_collapses(value: str) -> list[tuple[int, int, str]]:
    """(start, end, title) of every top-level ``[collapse=...]`` block."""
    spans: list[tuple[int, int, str]] = []
    stack: list[tuple[int, str]] = []
    for match in re.finditer(r"\[collapse=([^\]]*)\]|\[/collapse\]", value, re.I):
        if match.group(1) is not None:
            stack.append((match.end(), match.group(1)))
        elif stack:
            start, title = stack.pop()
            if not stack:
                spans.append((start, match.start(), title))
    return spans


def server_section(value: str) -> str:
    """The production-server block when a thread holds both server versions.

    Some NGA threads wrap the production and test-server notices in separate
    ``[collapse=正式服]`` / ``[collapse=体验服]`` blocks. The archive only wants
    the production one. A thread holding only the test-server block is not a
    production notice, so the whole post is returned instead of silently
    substituting the test-server text.
    """
    spans = top_level_collapses(value)
    for start, end, title in spans:
        if title.strip() == "正式服":
            body = value[start:end].strip()
            if body:
                return body
    return value


def drop_deleted(value: str) -> str:
    """Drop ``[del]...[/del]``: strikethrough is text the editor removed."""
    return DELETED.sub("", value)


def drop_image_collapses(value: str) -> str:
    """Remove ``[collapse=图片版公告]...[img]...`` duplicates.

    Non-image collapses are unwrapped: their text may be part of the notice.
    """

    def replace(match: re.Match[str]) -> str:
        title, body = match.group(1), match.group(2)
        if "图片" in title or "截图" in title:
            return ""
        return body

    return COLLAPSE.sub(replace, value)


def top_level_quotes(value: str) -> list[str]:
    """Content of every top-level ``[quote]`` block, nested quotes included."""
    spans: list[str] = []
    stack: list[int] = []
    for match in re.finditer(r"\[quote\]|\[/quote\]", value, re.I):
        if match.group(0).lower() == "[quote]":
            stack.append(match.end())
        elif stack:
            start = stack.pop()
            if not stack:
                spans.append(value[start : match.start()])
    return spans


def bbcode_to_text(value: str) -> str:
    """NGA's rendered BBCode to the notice's plain text.

    Only known BBCode tags are stripped. Square-bracket headings that belong to
    the notice itself (``[赛季新礼盒]``, ``[决斗勋章]``) are left untouched.
    """
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = IMG.sub("", text)
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.S | re.I)
    text = EMOTICON.sub("", text)
    text = text.replace(OFFICIAL_BULLET, "- ")
    text = KNOWN_TAGS.sub("", text)
    text = html_lib.unescape(text)

    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    kept: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            kept.append(line)
            blank = 0
        elif blank == 0:
            kept.append("")
            blank = 1
    return "\n".join(kept).strip()


def _quote_score(quote: str) -> int:
    text = bbcode_to_text(quote)
    score = len(text)
    if "===重要更新===" in text:
        score += 10_000
    if "亲爱的巫师" in text or "为了给大家" in text:
        score += 5_000
    if "维护" in text:
        score += 1_000
    return score if len(text) >= 200 else -1


def choose_quote(value: str) -> str | None:
    quotes = top_level_quotes(value)
    if not quotes:
        return None
    best = max(quotes, key=_quote_score)
    return best if _quote_score(best) >= 0 else None


def notice_text(fragment: str) -> str:
    section = server_section(fragment)
    cleaned = drop_image_collapses(section)
    cleaned = drop_deleted(cleaned)
    quote = choose_quote(cleaned)
    text = bbcode_to_text(quote if quote is not None else cleaned)
    if len(text) < 200:
        raise NgaError("first post has no usable notice text")
    return text


def _iso_posted(value: str) -> str:
    try:
        stamp = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise NgaError(f"unparseable post time {value!r}") from exc
    return stamp.replace(tzinfo=TZ).isoformat()


def check_notice_date(text: str, notice_date: str) -> None:
    match = DATE_IN_TEXT.search(text)
    if not match:
        raise NgaError(f"no maintenance date in text for {notice_date}")
    try:
        expected = date.fromisoformat(notice_date)
    except ValueError as exc:
        raise NgaError(f"bad notice date {notice_date!r}") from exc
    found = (int(match.group(1)), int(match.group(2)))
    if found != (expected.month, expected.day):
        raise NgaError(
            f"text date {found[0]}月{found[1]}日 does not match index {notice_date}"
        )


def check_posted_before(posted_at: str, notice_date: str) -> None:
    posted = datetime.fromisoformat(posted_at).date()
    expected = date.fromisoformat(notice_date)
    delta = (expected - posted).days
    if not 0 <= delta <= 7:
        raise NgaError(f"post time {posted_at} is not within a week before {notice_date}")


def parse_thread(
    page: str,
    *,
    tid: str,
    notice_date: str,
    page_sha256: str | None = None,
) -> dict[str, Any]:
    """One thread's first post as an archive record.

    The raw first-post HTML fragment is kept for provenance; the text is the
    cleaned notice. The body's maintenance date must match the index entry.
    """
    fragment = _post_fragment(page)
    subject_match = POSTSUBJECT.search(page)
    date_match = POSTDATE.search(page)
    if not subject_match or not date_match:
        raise NgaError(f"thread {tid} has no subject or post time")
    subject = _text(subject_match.group(1)) or f"NGA {tid}"
    posted_at = _iso_posted(_text(date_match.group(1)))

    author_match = POSTERINFO.search(page)
    author_uid = UID.search(author_match.group(0)).group(1) if author_match else None

    text = notice_text(fragment)
    check_notice_date(text, notice_date)
    check_posted_before(posted_at, notice_date)
    if not any(marker in text for marker in UPDATE_MARKERS):
        raise NgaError(f"thread {tid} has no update section")

    return {
        "id": tid,
        "source": "nga",
        "thread_url": f"{BASE_URL}/read.php?tid={tid}",
        "title": subject,
        "published_at": posted_at,
        "author_uid": author_uid,
        "notice_date": notice_date,
        "text": text,
        "raw_path": f"corpus/nga/raw/{tid}.html",
        "raw_sha256": sha256(fragment.encode("utf-8")),
        "page_sha256": page_sha256 or sha256(page.encode("utf-8")),
        "source_images": [url.strip() for url in IMG.findall(fragment)],
    }


def guest_cookie() -> str:
    return f"guestJs={int(time.time())}_{secrets.token_hex(3)}; lastpath=0"


def fetch_page(
    url: str,
    *,
    delay: float = 5.0,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 30.0,
) -> bytes:
    """Fetch one NGA page as public guest.

    NGA starts refusing guest requests after a burst, so the default pace is one
    request every five seconds and a 403 is retried after a 30-second pause with
    a fresh ``guestJs`` cookie.
    """
    session = http.session()
    status: int | None = None
    for attempt in range(1, retries + 1):
        response = session.get(
            url,
            headers={
                "Referer": f"{BASE_URL}/",
                "Cookie": guest_cookie(),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=timeout,
        )
        if response.status_code == 200:
            if delay:
                time.sleep(delay)
            return response.content
        status = response.status_code
        if response.status_code == 403 and attempt < retries:
            time.sleep(backoff * attempt)
            continue
        break
    raise NgaBlocked(f"NGA refused guest access ({status}) for {url}")


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as exc:
            raise NgaError(
                f"existing corpus {path} line {number} is not valid JSON: {exc}; "
                "fix or remove it before harvesting"
            ) from exc


def _read_page(path: Path) -> bytes:
    return path.read_bytes()


def _find_index_page(from_dir: Path) -> Path:
    for name in (f"{INDEX_TID}.html", "index.html", "thread.html"):
        candidate = from_dir / name
        if candidate.exists():
            return candidate
    raise NgaError(f"no index page in {from_dir} (expected {INDEX_TID}.html)")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def harvest(
    root: str | Path | None = None,
    *,
    before: str | None = None,
    from_dir: str | Path | None = None,
    index_html: str | Path | None = None,
    fetch: Fetcher | None = None,
    delay: float = 5.0,
    limit: int | None = None,
    allow_shrink: bool = False,
    out: str | Path | None = None,
) -> dict[str, Any]:
    """Snapshot the index and harvest every production notice.

    ``before`` and ``limit`` narrow the notices fetched this run. By default
    the result is unioned with the committed corpus by id, so a partial run
    never drops posts it did not visit; ``allow_shrink`` opts into replacing
    the corpus with exactly this run's set. An empty or malformed index or a
    malformed thread page raises :class:`NgaError` before any corpus file is
    rewritten.

    ``from_dir`` reads ``<tid>.html`` files instead of the network; the index
    page is looked up there unless ``index_html`` is given. ``out`` overrides
    the corpus directory (default ``<root>/corpus/nga``).

    ``raw_sha256`` is the stable hash of the first-post fragment.
    ``page_sha256`` is the hash of the page response that first captured that
    fragment; reruns keep it while the fragment is unchanged, so page chrome
    changing between visits does not create a diff.
    """
    root_path = Path(root) if root else REPO_ROOT
    boundary = before

    fetch = fetch or (lambda url: fetch_page(url, delay=delay))
    source_dir = Path(from_dir) if from_dir else None

    if index_html:
        index_bytes = Path(index_html).read_bytes()
        index_text = decode(index_bytes)
    elif source_dir:
        index_path = _find_index_page(source_dir)
        index_bytes = _read_page(index_path)
        index_text = decode(index_bytes)
    else:
        index_bytes = fetch(f"{BASE_URL}/read.php?tid={INDEX_TID}")
        index_text = decode(index_bytes)

    entries = parse_index(index_text)
    if not entries:
        raise NgaError(
            "NGA index has no maintenance entries (empty or unexpected page); "
            "refusing to write the corpus"
        )
    selected = [
        entry
        for entry in entries
        if entry["kind"] == "production"
        and entry["notice_date"]
        and (boundary is None or entry["notice_date"] < boundary)
    ]
    if limit is not None:
        selected = selected[:limit]

    seen: dict[str, str] = {}
    unique: list[dict[str, Any]] = []
    for entry in selected:
        previous = seen.get(entry["tid"])
        if previous:
            if previous != entry["notice_date"]:
                raise NgaError(
                    f"tid {entry['tid']} is indexed for both {previous} and {entry['notice_date']}"
                )
            continue
        seen[entry["tid"]] = entry["notice_date"]
        unique.append(entry)
    selected = unique

    corpus_dir = Path(out) if out else root_path / "corpus" / "nga"
    raw_dir = corpus_dir / "raw"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    posts_path = corpus_dir / "posts.jsonl"
    previous_posts: dict[str, dict[str, Any]] = {}
    if posts_path.exists():
        previous_posts = {
            post["id"]: post for post in _iter_jsonl(posts_path)
        }

    posts: list[dict[str, Any]] = []
    raw_files: dict[str, str] = {}
    for entry in selected:
        tid = entry["tid"]
        if source_dir:
            page_bytes = _read_page(source_dir / f"{tid}.html")
        else:
            page_bytes = fetch(f"{BASE_URL}/read.php?tid={tid}")
        page = decode(page_bytes)
        try:
            record = parse_thread(
                page,
                tid=tid,
                notice_date=entry["notice_date"],
                page_sha256=sha256(page_bytes),
            )
        except NgaError as exc:
            raise NgaError(f"tid {tid}: {exc}") from exc
        previous = previous_posts.get(tid)
        if previous and previous.get("raw_sha256") == record["raw_sha256"]:
            # The first-post fragment is unchanged; keep the page hash from the
            # snapshot that captured it, so a rerun stays byte-identical even
            # when NGA's page chrome changes.
            record["page_sha256"] = previous.get("page_sha256", record["page_sha256"])
        posts.append(record)
        raw_files[tid] = _post_fragment(page)

    if not posts and not previous_posts:
        raise NgaError(
            "no production notices were selected and no previous corpus exists; "
            "refusing to write an empty corpus"
        )
    if allow_shrink:
        merged_posts = posts
    else:
        # Keep every committed notice this partial run did not visit; fresh
        # records win for the ids this run did see.
        fetched_ids = {post["id"] for post in posts}
        merged_posts = posts + [
            post for post_id, post in previous_posts.items() if post_id not in fetched_ids
        ]
    if not merged_posts:
        raise NgaError("harvest produced no posts; refusing to write an empty corpus")
    merged_posts.sort(key=lambda post: (post["published_at"], post["id"]))
    dropped = sorted(set(previous_posts) - {post["id"] for post in merged_posts})

    posts_text = "".join(
        json.dumps(post, ensure_ascii=False, separators=(",", ":")) + "\n"
        for post in merged_posts
    )
    posts_changed = not posts_path.exists() or posts_path.read_text(encoding="utf-8") != posts_text

    raw_changed = False
    for tid, fragment in raw_files.items():
        path = raw_dir / f"{tid}.html"
        if not path.exists() or path.read_text(encoding="utf-8") != fragment:
            raw_changed = True

    index_path = corpus_dir / "index.json"
    previous_index: dict[str, Any] = {}
    if index_path.exists():
        try:
            previous_index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise NgaError(
                f"existing corpus {index_path} is not valid JSON: {exc}; "
                "fix or remove it before harvesting"
            ) from exc
    entries_unchanged = previous_index.get("entries") == entries
    index_payload = {
        "index_tid": INDEX_TID,
        "index_url": f"{BASE_URL}/read.php?tid={INDEX_TID}",
        "fetched_at": previous_index.get("fetched_at")
        if entries_unchanged
        else datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "page_sha256": previous_index.get("page_sha256")
        if entries_unchanged
        else sha256(index_bytes),
        "entry_count": len(entries),
        "production_count": sum(entry["kind"] == "production" for entry in entries),
        "test_count": sum(entry["kind"] == "test" for entry in entries),
        "entries": entries,
    }
    index_text_out = _json_text(index_payload)
    index_changed = (
        not index_path.exists() or index_path.read_text(encoding="utf-8") != index_text_out
    )

    changed = posts_changed or raw_changed or index_changed
    # Every corpus file is replaced atomically; a failure mid-run leaves the
    # previous complete file in place.
    if posts_changed:
        atomic.write_text(posts_path, posts_text)
    for tid, fragment in raw_files.items():
        path = raw_dir / f"{tid}.html"
        if not path.exists() or path.read_text(encoding="utf-8") != fragment:
            atomic.write_text(path, fragment)
    if index_changed:
        atomic.write_text(index_path, index_text_out)

    manifest_path = corpus_dir / "manifest.json"
    if changed or not manifest_path.exists():
        manifest = {
            "index_tid": INDEX_TID,
            "index_url": f"{BASE_URL}/read.php?tid={INDEX_TID}",
            "fetched_at": index_payload["fetched_at"],
            "post_count": len(merged_posts),
            "entry_count": len(entries),
            "boundary": boundary or "all",
            "notes": (
                "NGA is a community repost; text only; images are not mirrored. "
                "raw_sha256 is the stable content hash; page_sha256 is the first "
                "snapshot's page hash."
            ),
        }
        atomic.write_text(manifest_path, _json_text(manifest))

    return {
        "entry_count": len(entries),
        "post_count": len(merged_posts),
        "selected": len(selected),
        "dropped": dropped,
        "boundary": boundary or "all",
        "changed": changed,
        "path": str(posts_path),
    }
