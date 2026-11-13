"""Data layer for the text review desk.

The desk asks a human to settle one text question at a time. This module
builds that queue from primary data, keeps the append-only decision ledger and
materialises the verdicts: :func:`export` writes the search alias file and the
OCR decision export, while :func:`apply` promotes expert OCR verdicts into
``corrections/`` and writes ``review/terms.json``. It never rewrites
``edition/`` or ``dataset/``; those are rebuilt afterwards from the ledger.

Two issue families:

``term_conflict``
    The same thing may be written two ways in official notices. Counts and
    contexts come from the full text rows (post body plus image text, with the
    correction ledger applied) because precise search splits the two spellings
    into separate result sets.

``ocr_block``
    A correction the ledger recorded with ``confidence == "medium"``: the
    source image was partly obscured, truncated or otherwise uncertain. The
    issue carries the crop locator and a few neighbouring OCR lines.

The queue is deterministic: term issues sort by impact, OCR issues by risk.
Decisions live in ``review/decisions.jsonl`` (append-only JSONL, latest wins).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collectors import atomic

from . import archive, projection, text

Record = dict[str, Any]


@dataclass(frozen=True)
class TermPair:
    """One spelling pair and the evidence rules used to judge it.

    ``patterns_*`` are regular expressions; an empty tuple means "match the
    spelling literally". ``excludes_*`` removes matches that overlap one of
    those phrases. The default rules are deliberately literal: a false
    positive is worse than a missed context, because the desk asks a human to
    re-read every line it shows.
    """

    first: str
    second: str
    patterns_first: tuple[str, ...] = ()
    patterns_second: tuple[str, ...] = ()
    excludes_first: tuple[str, ...] = ()
    excludes_second: tuple[str, ...] = ()
    match_note: str = "按字面统计两种写法的全部出现"
    context_dependent: bool = False
    recommendation: str = ""


PLAIN_TERM_NOTE = "按字面统计两种写法的全部出现"

# The 14 spelling pairs observed in the archive. The order here is the order
# the desk presents them in before impact sorting.
TERM_PAIRS: tuple[TermPair, ...] = (
    TermPair("金锻", "金缎"),
    TermPair("石墩", "石礅"),
    TermPair("登陆", "登录"),
    TermPair("鲁本", "鲁伯"),
    TermPair("云雾飘渺", "云雾缥缈"),
    TermPair("凤吟凰鸣", "凰吟凤鸣"),
    TermPair(
        "进发",
        "迸发",
        context_dependent=True,
        recommendation="可能是不同词义（进发=出发，迸发=爆发）；请逐条看上下文。",
        match_note="按字面统计「进发」/「迸发」；两种词义可能不同，需逐条判断",
    ),
    TermPair(
        "换新",
        "焕新",
        patterns_first=(r"换新款", r"大奖换新", r"时装换新"),
        patterns_second=(r"焕新款", r"大奖焕新", r"时装焕新"),
        excludes_first=(r"兑换新增", r"更换新的", r"伙伴换新装"),
        match_note=(
            "只统计「换新款」「大奖换新」「时装换新」；"
            "不统计「兑换新增」「更换新的」「伙伴换新装」"
        ),
    ),
    TermPair("等级上线", "等级上限"),
    TermPair(
        "补获",
        "捕获",
        context_dependent=True,
        recommendation="可能是不同词义或 OCR 误识（补获=额外获得，捕获=抓获）；请逐条看上下文。",
        match_note="按字面统计「补获」/「捕获」；两种词义可能不同，需逐条判断",
    ),
    TermPair(
        "无暇",
        "无瑕",
        patterns_first=(r"无暇信念",),
        patterns_second=(r"无瑕信念",),
        match_note="只统计「无暇信念」/「无瑕信念」；不统计词条「无暇」「无瑕」",
    ),
    TermPair("银辉之匙", "银辉之钥"),
    TermPair("戈德利克", "戈德里克"),
    TermPair("长跑专卖店", "长袍专卖店"),
)


TERM_OPTIONS = [
    {"value": "same_alias", "label": "同一事物，检索合并", "description": "保留原文，建立 alias"},
    {"value": "different", "label": "不同事物，保持区分", "description": ""},
    {"value": "ocr_error", "label": "其中一个是 OCR 错误", "description": ""},
    {"value": "defer", "label": "稍后决定", "description": ""},
]

OCR_OPTIONS = [
    {"value": "accept", "label": "接受当前修正", "description": ""},
    {"value": "override", "label": "改为我输入的文字", "description": ""},
    {"value": "keep_original", "label": "保留 OCR 原文", "description": ""},
    {"value": "source_issue", "label": "印刷原样", "description": ""},
    {"value": "drop", "label": "整块不是正文", "description": ""},
    {"value": "defer", "label": "稍后", "description": ""},
]

DECISIONS_BY_TYPE: dict[str, frozenset[str]] = {
    "term_conflict": frozenset({"same_alias", "different", "ocr_error", "defer"}),
    "ocr_block": frozenset(
        {"accept", "override", "keep_original", "source_issue", "drop", "defer"}
    ),
}

# Pure footer ornament is not body text and does not need a human verdict.
DECORATION_MARKERS = ("页脚装饰水印", "HARRY POTTER", "letter-spaced")

# Reasons that describe damage to the source text rather than a wording choice.
# The ledger writes reasons in both Chinese and English, so both are matched.
OCR_DAMAGE_MARKERS = ("水印", "截断", "覆盖", "watermark", "truncat", "covered")
# Non-damage reasons that describe typography, quotes or layout rather than
# uncertain wording. Everything else is ``other``.
OCR_STYLE_MARKERS = (
    "style",
    "ascii",
    "quote",
    "引号",
    "标点",
    "字体",
    "字形",
    "letter",
    "spacing",
    "typography",
    "格式",
    "排版",
)

MAX_CONTEXTS_PER_VARIANT = 8


# --------------------------------------------------------------------------- #
# Full text rows
# --------------------------------------------------------------------------- #


def load_fulltext_rows(root: str | Path | None = None) -> list[Record]:
    """Every update notice with its body, per-image text and canonical flag.

    Dashen rows combine the post body with the effective image text (OCR plus
    the correction ledger); NGA rows carry the repost body. This is the same
    projection the old checkpage used, kept here so the review desk does not
    depend on that page.
    """
    base = Path(root) if root else archive.REPO_ROOT
    rows: list[Record] = []
    for source in archive.SOURCES.values():
        if not (base / source.corpus).is_file() or not (base / source.selection).is_file():
            continue
        for post, selection in archive.selected_posts(base, "update", source.name):
            feed = post["id"]
            notice = archive.load_ocr(feed, base) if source.ocr else None
            ledger = archive.load_ledger(feed, base) if source.ocr else None
            if notice:
                try:
                    sections = projection.effective_sections(notice, ledger)
                except ValueError:
                    # A ledger that no longer matches the OCR must not take the
                    # whole desk down: show the raw OCR reading instead, and
                    # let the export mark affected decisions stale.
                    sections = projection.effective_sections(notice, None)
            else:
                sections = []
            body = text.normalize_body(post.get("text") or "")
            image_text = "\n".join(line for _, lines in sections for line in lines)
            rows.append(
                {
                    "id": archive.notice_id(source.name, feed),
                    "source": source.name,
                    "title": post.get("title") or "",
                    "notice_date": selection.get("notice_date") or "",
                    "published_at": post.get("published_at") or "",
                    "url": archive.post_url(post),
                    "body": body,
                    "image_text": image_text,
                    "sections": [
                        {"image": index, "lines": lines} for index, lines in sections
                    ],
                    "images": [
                        image["path"]
                        for image in (notice or {}).get("images", [])
                        if image.get("path")
                    ],
                }
            )
    rows.sort(key=lambda row: (row["published_at"], row["id"]))
    projection.assign_canonical(rows)
    return rows


def _row_streams(row: Record) -> list[tuple[str, int | None, list[str]]]:
    """Text streams of one row as ``(section, image, lines)``.

    The body is one stream; each image with effective text is another, so a
    context's before/after never crosses an image boundary.
    """
    streams: list[tuple[str, int | None, list[str]]] = []
    body = row.get("body") or ""
    if body:
        streams.append(("正文", None, body.split("\n")))
    covered = False
    for section in row.get("sections") or []:
        lines = section.get("lines") or []
        if lines:
            streams.append(("图片文字", section.get("image"), lines))
            covered = True
    if not covered and row.get("image_text"):
        streams.append(("图片文字", None, row["image_text"].split("\n")))
    return streams


Pattern = tuple[re.Pattern[str], re.Pattern[str] | None]


def _term_patterns(pair: TermPair, variant: str) -> Pattern:
    """Include/exclude regexes for one spelling of ``pair``."""
    if variant == pair.first:
        includes = pair.patterns_first or (re.escape(variant),)
        excludes = pair.excludes_first
    else:
        includes = pair.patterns_second or (re.escape(variant),)
        excludes = pair.excludes_second
    include = re.compile("|".join(f"(?:{pattern})" for pattern in includes))
    exclude = (
        re.compile("|".join(f"(?:{pattern})" for pattern in excludes))
        if excludes
        else None
    )
    return include, exclude


def _match_count(value: str, pattern: Pattern) -> int:
    """Count include matches that do not overlap an excluded phrase."""
    include, exclude = pattern
    excluded = [match.span() for match in exclude.finditer(value)] if exclude else []
    count = 0
    for match in include.finditer(value):
        start, end = match.span()
        if any(start < other_end and other_start < end for other_start, other_end in excluded):
            continue
        count += 1
    return count


def _variant_count(row: Record, pattern: Pattern) -> int:
    return _match_count(row.get("body") or "", pattern) + _match_count(
        row.get("image_text") or "", pattern
    )


def _term_contexts(
    rows: list[Record], variants: list[str], patterns: dict[str, Pattern]
) -> dict[str, list[Record]]:
    """Up to :data:`MAX_CONTEXTS_PER_VARIANT` line contexts per variant."""
    contexts: dict[str, list[Record]] = {variant: [] for variant in variants}
    for row in rows:
        for section, image, lines in _row_streams(row):
            for position, line in enumerate(lines):
                for variant in variants:
                    if len(contexts[variant]) >= MAX_CONTEXTS_PER_VARIANT:
                        continue
                    if not _match_count(line, patterns[variant]):
                        continue
                    contexts[variant].append(
                        {
                            "variant": variant,
                            "notice_id": row.get("id") or "",
                            "notice_date": row.get("notice_date") or "",
                            "title": row.get("title") or "",
                            "source": row.get("source") or "",
                            "section": section,
                            "image": image,
                            "line": line,
                            "before": lines[position - 1] if position > 0 else "",
                            "after": lines[position + 1] if position + 1 < len(lines) else "",
                        }
                    )
    return contexts


def _recommendation(pair: TermPair, minor: Record, major: Record) -> str:
    if pair.context_dependent and pair.recommendation:
        return pair.recommendation
    if minor["count"] <= 2:
        tail = "稀有写法数量很少，可能是官方笔误或 OCR 混用；倾向合并检索并保留原文。"
    elif minor["count"] * 2 >= major["count"]:
        tail = "两种写法数量接近，不宜只看数量；请对照上下文确认是否同一事物。"
    else:
        tail = "稀有写法集中在少数公告，可能是官方自身不一致；倾向合并检索并保留原文。"
    return (
        f"稀有写法「{minor['text']}」{minor['count']} 处、{minor['notices']} 篇；"
        f"另一种「{major['text']}」{major['count']} 处、{major['notices']} 篇。"
        f"{tail}系统建议仅供参考，以专家判断为准。"
    )


def term_issues(rows: Iterable[Record]) -> list[Record]:
    """One issue per pair that actually appears on both sides of ``rows``."""
    materialised = list(rows)
    issues: list[Record] = []
    for pair in TERM_PAIRS:
        patterns = {
            pair.first: _term_patterns(pair, pair.first),
            pair.second: _term_patterns(pair, pair.second),
        }
        variants: list[Record] = []
        for variant in (pair.first, pair.second):
            count = 0
            notices = 0
            for row in materialised:
                seen = _variant_count(row, patterns[variant])
                if seen:
                    count += seen
                    notices += 1
            variants.append({"text": variant, "count": count, "notices": notices})

        if not all(variant["count"] for variant in variants):
            # A pair is only a conflict when both spellings are in use.
            # This also drops 无暇/无瑕 when only the lexicon word appears.
            continue

        major = max(variants, key=lambda variant: (variant["count"], variant["notices"]))
        minor = min(
            variants,
            key=lambda variant: (variant["count"], variant["notices"]),
        )
        if minor is major:
            # Equal counts: pick the second spelling as the labelled rare one
            # so the presentation remains stable.
            minor = variants[1]
            major = variants[0]

        if pair.context_dependent:
            severity = "medium"
        elif minor["count"] >= 3 or minor["notices"] >= 5:
            severity = "high"
        else:
            severity = "medium"
        contexts = _term_contexts(materialised, [pair.first, pair.second], patterns)
        issues.append(
            {
                "id": f"term:{pair.first}|{pair.second}",
                "type": "term_conflict",
                "severity": severity,
                "title": f"「{pair.first}」与「{pair.second}」",
                "question": "这两种写法指同一个事物吗？检索时是否合并？",
                "why": "两种写法都出现在官方原文；当前精确检索会把它们拆成两组。",
                "match_note": pair.match_note,
                "variants": variants,
                "impact": {
                    "minor_count": minor["count"],
                    "minor_notices": minor["notices"],
                    "total_notices": major["notices"],
                    "search_gain": minor["count"],
                },
                "contexts": contexts[pair.first] + contexts[pair.second],
                "options": [dict(option) for option in TERM_OPTIONS],
                "recommendation": _recommendation(pair, minor, major),
            }
        )
    return issues


# --------------------------------------------------------------------------- #
# OCR review issues
# --------------------------------------------------------------------------- #


def _notice_id(value: str, fallback_stem: str) -> str:
    source, _, native = value.partition(".")
    if native and source in archive.SOURCES:
        return value
    return archive.notice_id("dashen", value or fallback_stem)


def _is_decoration(reason: str) -> bool:
    folded = reason.casefold()
    return any(marker.casefold() in folded for marker in DECORATION_MARKERS)


def _ocr_risk(reason: str) -> str:
    """Whether the uncertainty comes from source damage, typography or neither."""
    folded = reason.casefold()
    if any(marker.casefold() in folded for marker in OCR_DAMAGE_MARKERS):
        return "source_damage"
    if any(marker.casefold() in folded for marker in OCR_STYLE_MARKERS):
        return "style"
    return "other"


def _ocr_cache_read(base: Path, notice: str, cache: dict[str, Record | None]) -> Record | None:
    """The OCR record for ``notice``, or ``None`` when it is not on disk."""
    path = archive.ocr_path(notice, base)
    key = path.as_posix()
    if key not in cache:
        cache[key] = archive.read_json(path) if path.is_file() else None
    return cache[key]


def _ocr_images(notice_record: Record | None) -> list[Record]:
    return list((notice_record or {}).get("images") or [])


def _ocr_image(images: list[Record], image_index: int) -> Record | None:
    for image in images:
        if image.get("index") == image_index:
            return image
    if 0 < image_index <= len(images):
        return images[image_index - 1]
    return None


def _block_texts(blocks: list[Record]) -> list[str]:
    return [str(block.get("text") or "").strip() for block in blocks]


def _ocr_context(
    images: list[Record], image_index: int, block_index: int
) -> tuple[list[str], list[str]]:
    image = _ocr_image(images, image_index)
    if not image:
        return [], []
    blocks = _block_texts(image.get("blocks") or [])
    before = [value for value in blocks[max(0, block_index - 2) : block_index] if value]
    after = [value for value in blocks[block_index + 1 : block_index + 3] if value]
    return before[-2:], after[:2]


def _ocr_block_text(
    base: Path, notice: str, image_index: int, block_index: int
) -> str | None:
    record = _ocr_cache_read(base, notice, {})
    images = _ocr_images(record)
    image = _ocr_image(images, image_index)
    if not image:
        return None
    blocks = image.get("blocks") or []
    if not 0 <= block_index < len(blocks):
        return None
    return str(blocks[block_index].get("text") or "")


def _ocr_issues(root: Path, rows: list[Record]) -> list[Record]:
    rows_by_id = {row["id"]: row for row in rows}
    cache: dict[str, Record | None] = {}
    issues: list[Record] = []
    for stem, ledger in archive.iter_ledgers(root):
        notice = _notice_id(str(ledger.get("id") or ""), stem)
        row = rows_by_id.get(notice, {})
        for correction in ledger.get("corrections") or []:
            if correction.get("confidence") != "medium":
                continue
            if correction.get("drop"):
                continue
            corrected = correction.get("corrected")
            if not isinstance(corrected, str) or not corrected:
                continue
            reason = str(correction.get("reason") or "")
            if _is_decoration(reason):
                continue
            image_index = correction.get("image_index")
            block_index = correction.get("block_index")
            if not isinstance(image_index, int) or not isinstance(block_index, int):
                continue
            original = correction.get("original")
            if not isinstance(original, str):
                original = ""
            images = _ocr_images(_ocr_cache_read(root, notice, cache))
            before, after = _ocr_context(images, image_index, block_index)
            issues.append(
                {
                    "id": f"ocr:{notice}:{image_index}:{block_index}",
                    "type": "ocr_block",
                    "severity": "medium",
                    "risk": _ocr_risk(reason),
                    "title": f"{notice} · 图 {image_index} · block {block_index}",
                    "question": "这个 block 的源图文字是什么？",
                    "why": f"confidence=medium；{reason}",
                    "notice": notice,
                    "notice_date": row.get("notice_date") or "",
                    "notice_title": row.get("title") or "",
                    "image_index": image_index,
                    "block_index": block_index,
                    "original": original,
                    "corrected": corrected,
                    "reason": reason,
                    "confidence": "medium",
                    "crop": {
                        "notice": notice,
                        "image": image_index,
                        "block": block_index,
                        "scale": 2,
                    },
                    "context_before": before,
                    "context_after": after,
                    "options": [dict(option) for option in OCR_OPTIONS],
                }
            )
    return issues


def ocr_issues(root: str | Path | None = None) -> list[Record]:
    """Medium-confidence, non-drop corrections that still need a human verdict."""
    base = Path(root) if root else archive.REPO_ROOT
    return _ocr_issues(base, load_fulltext_rows(base))


def _archived_ocr_issues(
    base: Path,
    rows: list[Record],
    current_ids: set[str],
    decisions: Mapping[str, Record],
) -> list[Record]:
    """Rebuild decided OCR issues that the medium queue no longer contains.

    An applied override promotes its correction to ``high`` and therefore
    removes the block from :func:`_ocr_issues`. The desk still has to show the
    verdict in its history, so every non-deferred OCR decision without a live
    issue is rebuilt from the ledger as a read-only, ``archived`` issue. The
    fields mirror a normal OCR issue; ``options`` stays empty because there is
    nothing left to decide.
    """
    rows_by_id = {row["id"]: row for row in rows}
    cache: dict[str, Record | None] = {}
    ledgers: dict[str, Record | None] = {}
    issues: list[Record] = []
    for issue_id in sorted(decisions):
        if issue_id in current_ids or not issue_id.startswith("ocr:"):
            continue
        record = decisions[issue_id]
        decision = str(record.get("decision") or "")
        if not decision or decision == "defer":
            continue
        try:
            notice, image_index, block_index = _ocr_locator(issue_id)
        except ValueError:
            continue
        feed = archive.feed_id(notice)
        images = _ocr_images(_ocr_cache_read(base, notice, cache))
        image = _ocr_image(images, image_index)
        blocks = (image or {}).get("blocks") or []
        current = (
            str(blocks[block_index].get("text") or "")
            if 0 <= block_index < len(blocks)
            else None
        )
        if feed not in ledgers:
            ledgers[feed] = archive.load_ledger(feed, base)
        entry = _find_correction(ledgers[feed], image_index, block_index)
        if entry is not None:
            original = str(entry.get("original") or current or "")
            corrected = "" if entry.get("drop") else str(entry.get("corrected") or "")
            reason = str(entry.get("reason") or "")
            confidence = str(entry.get("confidence") or "high")
        else:
            original = str(record.get("original") or current or "")
            corrected = (
                "" if decision in ("keep_original", "drop")
                else str(record.get("corrected") or original)
            )
            reason = str(record.get("note") or "专家裁决已应用")
            confidence = "high"
        before, after = _ocr_context(images, image_index, block_index)
        row = rows_by_id.get(notice, {})
        issues.append(
            {
                "id": issue_id,
                "type": "ocr_block",
                "severity": "medium",
                "risk": _ocr_risk(reason),
                "title": f"{notice} · 图 {image_index} · block {block_index}",
                "question": "这个 block 的源图文字是什么？",
                "why": f"confidence={confidence}；{reason}",
                "notice": notice,
                "notice_date": row.get("notice_date") or "",
                "notice_title": row.get("title") or "",
                "image_index": image_index,
                "block_index": block_index,
                "original": original,
                "corrected": corrected,
                "reason": reason,
                "confidence": confidence,
                "crop": {
                    "notice": notice,
                    "image": image_index,
                    "block": block_index,
                    "scale": 2,
                },
                "context_before": before,
                "context_after": after,
                "options": [],
                "archived": True,
                "applied": True,
            }
        )
    return issues


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #


def _issue_kind(issue_id: str) -> str:
    if issue_id.startswith("term:"):
        return "term_conflict"
    if issue_id.startswith("ocr:"):
        return "ocr_block"
    raise ValueError(f"unknown issue id: {issue_id!r}")


def _index_decisions(
    decisions: Mapping[str, Record] | Iterable[Record] | None,
) -> dict[str, Record]:
    if decisions is None:
        return {}
    if isinstance(decisions, Mapping):
        return {str(key): dict(value) for key, value in decisions.items()}
    latest: dict[str, Record] = {}
    for record in decisions:
        issue_id = record.get("issue_id")
        if issue_id:
            latest[str(issue_id)] = dict(record)
    return latest


def _term_sort_key(issue: Record) -> tuple[int, int, int, str]:
    impact = issue.get("impact") or {}
    return (
        0 if issue.get("severity") == "high" else 1,
        -int(impact.get("minor_count") or 0),
        -int(impact.get("minor_notices") or 0),
        issue.get("id") or "",
    )


def _ocr_sort_key(issue: Record) -> tuple[int, str]:
    return (0 if issue.get("risk") == "source_damage" else 1, issue.get("id") or "")


def build_queue(
    root: str | Path | None = None,
    decisions: Mapping[str, Record] | Iterable[Record] | None = None,
) -> Record:
    """Merge term and OCR issues, sorted for the desk, with progress stats.

    Term conflicts come first, ordered by impact (the rare spelling's count and
    notice spread). OCR issues follow, source damage first. OCR never uses
    ``high`` severity, so the high-priority filter is terminology only.

    Decided OCR blocks that no longer need a verdict (an override promoted the
    correction to ``high``, or a ``keep_original``/``drop`` removed it) are
    appended as read-only ``archived`` issues so the decision history stays
    visible and ``resolved`` counts every adjudicated issue.
    """
    base = Path(root) if root else archive.REPO_ROOT
    rows = load_fulltext_rows(base)
    terms = term_issues(rows)
    terms.sort(key=_term_sort_key)
    ocr = _ocr_issues(base, rows)
    ocr.sort(key=_ocr_sort_key)

    latest = (
        load_decisions(default_decisions_path(base))
        if decisions is None
        else _index_decisions(decisions)
    )
    live = terms + ocr
    archived = _archived_ocr_issues(base, rows, {issue["id"] for issue in live}, latest)
    archived.sort(key=_ocr_sort_key)
    issues = live + archived

    resolved = 0
    deferred = 0
    high_pending = 0
    term_pending = 0
    ocr_pending = 0
    for issue in issues:
        record = latest.get(issue["id"])
        decided = bool(record) and record.get("decision") != "defer"
        if decided:
            resolved += 1
        else:
            if record:
                deferred += 1
            if issue["severity"] == "high":
                high_pending += 1
            if issue["type"] == "term_conflict":
                term_pending += 1
            else:
                ocr_pending += 1

    total = len(issues)
    stats = {
        "total": total,
        "pending": total - resolved,
        "resolved": resolved,
        "deferred": deferred,
        "high": high_pending,
        "high_total": sum(1 for issue in issues if issue["severity"] == "high"),
        "term": sum(1 for issue in issues if issue["type"] == "term_conflict"),
        "ocr": sum(1 for issue in issues if issue["type"] == "ocr_block"),
        "term_pending": term_pending,
        "ocr_pending": ocr_pending,
        "source_damage": sum(1 for issue in issues if issue.get("risk") == "source_damage"),
    }
    return {"issues": issues, "stats": stats}


# --------------------------------------------------------------------------- #
# Decision ledger
# --------------------------------------------------------------------------- #


def default_decisions_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root else archive.REPO_ROOT
    return base / "review" / "decisions.jsonl"


def load_decisions(path: str | Path | None = None) -> dict[str, Record]:
    """Latest decision per issue id. File order is authoritative."""
    target = Path(path) if path else default_decisions_path()
    latest: dict[str, Record] = {}
    if not target.is_file():
        return latest
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{target}:{number}: invalid JSON: {error}") from error
        if not isinstance(record, dict) or not record.get("issue_id"):
            raise ValueError(f"{target}:{number}: decision needs an issue_id")
        latest[str(record["issue_id"])] = record
    return latest


def _validate_decision(kind: str, decision: str, payload: Record) -> None:
    allowed = DECISIONS_BY_TYPE.get(kind)
    if allowed is None:
        raise ValueError(f"unknown issue type: {kind!r}")
    if decision not in allowed:
        raise ValueError(f"decision {decision!r} is not valid for {kind}")
    if kind == "term_conflict" and decision == "same_alias":
        preferred = payload.get("preferred")
        if not isinstance(preferred, str) or not preferred:
            raise ValueError("same_alias needs a non-empty preferred spelling")
        aliases = payload.get("aliases")
        if aliases is not None and not (
            isinstance(aliases, list) and all(isinstance(alias, str) for alias in aliases)
        ):
            raise ValueError("aliases must be a list of strings")
    if kind == "ocr_block" and decision == "override":
        corrected = payload.get("corrected")
        if not isinstance(corrected, str) or not corrected:
            raise ValueError("override needs a non-empty corrected text")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def save_decision(
    issue_id: str,
    decision: str,
    payload: Record | None = None,
    note: str = "",
    path: str | Path | None = None,
) -> Record:
    """Append one decision; the last record for an issue id wins on load."""
    kind = _issue_kind(issue_id)
    body = dict(payload or {})
    _validate_decision(kind, decision, body)
    record = {
        **body,
        "issue_id": issue_id,
        "type": kind,
        "decision": decision,
        "note": note or "",
        "decided_at": _now(),
    }
    target = Path(path) if path else default_decisions_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


# --------------------------------------------------------------------------- #
# Decision materialisation
# --------------------------------------------------------------------------- #

# Appended to a ledger reason when the review desk, not an audit pass, set the
# text. The marker is also how a rerun recognises its own work.
REVIEW_REASON_MARKER = "review desk 专家裁决"

def _ordered_decisions(
    decisions: Mapping[str, Record], issues: Mapping[str, Record]
) -> list[Record]:
    """Decisions in desk order, then any decision whose issue left the queue.

    An applied ``override`` raises a correction's confidence, so the issue
    disappears from the medium-confidence queue. Those decisions still belong
    in the export and in a rerun, so they follow the queue in stable id order.
    """
    ordered = [decisions[issue_id] for issue_id in issues if issue_id in decisions]
    extras = sorted(issue_id for issue_id in decisions if issue_id not in issues)
    ordered.extend(decisions[issue_id] for issue_id in extras)
    return ordered


def _ocr_locator(issue_id: str) -> tuple[str, int, int]:
    """Split ``ocr:<notice>:<image>:<block>`` into its three locator fields."""
    parts = issue_id.split(":")
    if len(parts) != 4 or parts[0] != "ocr":
        raise ValueError(f"malformed OCR issue id: {issue_id!r}")
    try:
        return parts[1], int(parts[2]), int(parts[3])
    except ValueError as error:
        raise ValueError(f"malformed OCR issue id: {issue_id!r}") from error


def _find_correction(
    ledger: Record | None, image_index: int, block_index: int
) -> Record | None:
    if not ledger:
        return None
    for correction in ledger.get("corrections") or []:
        if correction.get("insert"):
            continue
        if (
            correction.get("image_index") == image_index
            and correction.get("block_index") == block_index
        ):
            return correction
    return None


def _remove_corrections(ledger: Record, image_index: int, block_index: int) -> int:
    corrections = ledger.get("corrections")
    if not isinstance(corrections, list):
        return 0
    keep = [
        correction
        for correction in corrections
        if correction.get("insert")
        or correction.get("image_index") != image_index
        or correction.get("block_index") != block_index
    ]
    removed = len(corrections) - len(keep)
    if removed:
        ledger["corrections"] = keep
    return removed


def _review_reason(reason: str) -> str:
    """Append the desk marker once, even when a ledger reason is empty."""
    text = str(reason or "").strip()
    if not text:
        return REVIEW_REASON_MARKER
    if REVIEW_REASON_MARKER in text:
        return text
    return f"{text}；{REVIEW_REASON_MARKER}"


def _variant_texts(issue: Record | None, issue_id: str) -> list[str]:
    if issue:
        return [
            str(variant.get("text") or "")
            for variant in issue.get("variants") or []
            if variant.get("text")
        ]
    return [part for part in issue_id[len("term:"):].split("|") if part]


def _term_alias(issue: Record | None, record: Record) -> Record | None:
    """One ``same_alias`` entry for ``review/terms.json``."""
    if record.get("decision") != "same_alias":
        return None
    issue_id = str(record["issue_id"])
    preferred = record.get("preferred")
    if not isinstance(preferred, str) or not preferred:
        raise ValueError(f"{issue_id}: same_alias decision is missing preferred")
    aliases = record.get("aliases")
    if not isinstance(aliases, list):
        aliases = _variant_texts(issue, issue_id)
    aliases = [
        alias
        for alias in dict.fromkeys(str(alias) for alias in aliases)
        if alias and alias != preferred
    ]
    return {
        "preferred": preferred,
        "aliases": aliases,
        "issue_id": issue_id,
        "note": record.get("note") or "",
        "decided_at": record.get("decided_at") or "",
    }


def _term_different(issue: Record | None, record: Record) -> Record:
    """One ``different`` entry: the decision is authoritative, never dropped."""
    issue_id = str(record["issue_id"])
    return {
        "terms": _variant_texts(issue, issue_id),
        "issue_id": issue_id,
        "note": record.get("note") or "",
        "decided_at": record.get("decided_at") or "",
    }


def _write_terms(
    base: Path,
    generated_at: str,
    terms: list[Record],
    different: list[Record],
) -> Path:
    path = base / "review" / "terms.json"
    atomic.write_text(
        path,
        json.dumps(
            {"generated_at": generated_at, "terms": terms, "different": different},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return path


def _effective_corrected(
    issue: Record | None,
    record: Record,
    entry: Record | None = None,
    original: str = "",
) -> str:
    decision = record.get("decision")
    if decision == "override":
        return str(record.get("corrected") or "")
    if decision == "keep_original":
        return original
    if decision == "drop":
        return ""
    if issue is not None:
        return str(issue.get("corrected") or "")
    if entry is not None:
        return str(entry.get("corrected") or "")
    return original


def _decision_applied(
    decision: str,
    record: Record,
    entry: Record | None,
    current: str | None,
    applicable: bool,
) -> bool:
    """Whether the ledger currently carries this verdict's outcome."""
    if not applicable or current is None:
        return False
    if decision == "override":
        return (
            entry is not None
            and not entry.get("drop")
            and entry.get("corrected") == record.get("corrected")
        )
    if decision == "keep_original":
        return entry is None
    if decision == "drop":
        return entry is not None and bool(entry.get("drop"))
    if decision in ("accept", "source_issue"):
        return entry is not None and not entry.get("drop") and bool(entry.get("corrected"))
    return False


def export(root: str | Path | None = None) -> Record:
    """Write the search aliases and the OCR decision export.

    ``review/terms.json`` carries every non-defer term verdict: ``terms`` for
    ``same_alias`` and ``different`` for the pairs the expert keeps apart.
    ``review/ocr-decisions.jsonl`` carries every non-defer OCR verdict and
    checks its ``original`` against the OCR currently on disk. A mismatch is
    marked ``stale`` so it is never applied silently. ``applied`` says whether
    the ledger now reflects that verdict (an override's text, an accept that
    still matches, a drop that is present, a removed correction for
    ``keep_original``).
    """
    base = Path(root) if root else archive.REPO_ROOT
    decisions = load_decisions(default_decisions_path(base))
    queue = build_queue(base, decisions=decisions)
    issues = {issue["id"]: issue for issue in queue["issues"]}
    ledgers = {stem: ledger for stem, ledger in archive.iter_ledgers(base)}
    generated_at = _now()

    terms: list[Record] = []
    different: list[Record] = []
    ocr_records: list[Record] = []
    stale = 0
    for record in _ordered_decisions(decisions, issues):
        issue_id = str(record["issue_id"])
        issue = issues.get(issue_id)
        if _issue_kind(issue_id) == "term_conflict":
            entry = _term_alias(issue, record)
            if entry:
                terms.append(entry)
            elif record.get("decision") == "different":
                different.append(_term_different(issue, record))
            continue
        if record.get("decision") == "defer":
            continue
        notice, image_index, block_index = _ocr_locator(issue_id)
        current = _ocr_block_text(base, notice, image_index, block_index)
        entry = _find_correction(
            ledgers.get(archive.feed_id(notice)), image_index, block_index
        )
        original = (
            str(issue.get("original") or "")
            if issue is not None
            else str((entry or {}).get("original") or current or "")
        )
        applicable = current is not None and current == original
        if not applicable:
            stale += 1
        ocr_records.append(
            {
                "issue_id": issue_id,
                "notice": notice,
                "image_index": image_index,
                "block_index": block_index,
                "decision": record.get("decision"),
                "original": original,
                "corrected": _effective_corrected(issue, record, entry, original),
                "current_ocr": current,
                "applicable": applicable,
                "status": "applicable" if applicable else "stale",
                "applied": _decision_applied(
                    str(record.get("decision")),
                    record,
                    entry,
                    current,
                    applicable,
                ),
                "note": record.get("note") or "",
                "decided_at": record.get("decided_at") or "",
            }
        )

    terms_path = _write_terms(base, generated_at, terms, different)
    ocr_path = base / "review" / "ocr-decisions.jsonl"
    ocr_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in ocr_records
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "generated_at": generated_at,
        "terms": len(terms),
        "different": len(different),
        "ocr": len(ocr_records),
        "applied": sum(1 for record in ocr_records if record["applied"]),
        "stale": stale,
        "terms_path": str(terms_path),
        "ocr_decisions_path": str(ocr_path),
    }


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def _new_ledger(feed: str) -> Record:
    return {
        "id": f"dashen.{feed}",
        "source_ocr": f"derived/ocr/dashen/{feed}.json",
        "verified_by": "review-desk",
        "verification": "expert review decisions from review/decisions.jsonl",
        "corrections": [],
    }


def _correction_base(
    notice: str,
    image_index: int,
    block_index: int,
    original: str,
    reason: str,
    issue_id: str,
) -> Record:
    return {
        "image_index": image_index,
        "block_index": block_index,
        "original": original,
        "reason": _review_reason(reason),
        "confidence": "high",
        "via": "expert-review",
        "review_issue_id": issue_id,
        "recrop": f"python -m tools.cli crop {notice} {image_index} {block_index}",
    }


def apply(root: str | Path | None = None, dry_run: bool = False) -> Record:
    """Promote the latest decisions into the correction ledger and aliases.

    Term verdicts are materialised in ``review/terms.json`` (``same_alias``
    and ``different``). OCR verdicts change ``corrections/dashen/``:

    * ``accept`` / ``source_issue`` - no ledger change;
    * ``override`` - replace ``corrected`` with the expert text and mark the
      entry ``high`` / ``expert-review``;
    * ``keep_original`` - remove the correction so the raw OCR text wins;
    * ``drop`` - mark the block as not edition text;
    * ``defer`` - leave it in the queue.

    Every OCR verdict is checked against the OCR block currently on disk. A
    mismatched ``original`` is reported as ``stale`` and never written. The
    operation is idempotent: an override whose text already matches, a drop
    that is already present or a ``keep_original`` with no correction counts
    as ``noop`` and leaves the ledger bytes untouched.

    Ledgers are replaced atomically. ``review/apply-report.json`` records the
    counts, changed ledger files and anomalies; ``dry_run`` computes the same
    report without writing anything.
    """
    base = Path(root) if root else archive.REPO_ROOT
    decisions = load_decisions(default_decisions_path(base))
    queue = build_queue(base, decisions=decisions)
    issues = {issue["id"]: issue for issue in queue["issues"]}
    ledgers = {stem: ledger for stem, ledger in archive.iter_ledgers(base)}
    generated_at = _now()

    report: Record = {
        "generated_at": generated_at,
        "dry_run": dry_run,
        "term": {
            "same_alias": 0,
            "different": 0,
            "ocr_error": 0,
            "defer": 0,
            "skipped": 0,
        },
        "ocr": {
            "accept": 0,
            "override": 0,
            "keep_original": 0,
            "source_issue": 0,
            "drop": 0,
            "defer": 0,
            "applied": 0,
            "noop": 0,
            "stale": 0,
            "skipped": 0,
        },
        "applied": 0,
        "noop": 0,
        "stale": 0,
        "skipped": 0,
        "stale_items": [],
        "skipped_items": [],
        "ledger_files": [],
        "problems": [],
    }
    terms: list[Record] = []
    different: list[Record] = []
    dirty: set[str] = set()

    def skip(issue_id: str, reason: str, kind: str | None = None) -> None:
        report["skipped"] += 1
        if kind:
            report[kind]["skipped"] += 1
        report["skipped_items"].append({"issue_id": issue_id, "reason": reason})

    def stale(
        issue_id: str, notice: str, image_index: int, block_index: int,
        expected: str | None, current: str | None,
    ) -> None:
        report["stale"] += 1
        report["ocr"]["stale"] += 1
        report["stale_items"].append(
            {
                "issue_id": issue_id,
                "notice": notice,
                "image_index": image_index,
                "block_index": block_index,
                "expected": expected,
                "current": current,
            }
        )

    for record in _ordered_decisions(decisions, issues):
        issue_id = str(record.get("issue_id") or "")
        try:
            kind = _issue_kind(issue_id)
        except ValueError:
            skip(issue_id, "unknown issue id")
            continue
        decision = str(record.get("decision") or "")
        issue = issues.get(issue_id)

        if kind == "term_conflict":
            allowed = DECISIONS_BY_TYPE["term_conflict"]
            if decision not in allowed:
                skip(issue_id, f"unknown term decision {decision!r}", "term")
                continue
            if decision == "defer":
                report["term"]["defer"] += 1
                skip(issue_id, "deferred")
                continue
            report["term"][decision] += 1
            try:
                if decision == "same_alias":
                    entry = _term_alias(issue, record)
                    if entry is None:  # pragma: no cover - guarded by decision
                        raise ValueError("same_alias did not produce an alias")
                    terms.append(entry)
                    report["applied"] += 1
                elif decision == "different":
                    different.append(_term_different(issue, record))
                    report["applied"] += 1
                else:  # ocr_error: the decision is primary, nothing to materialise
                    report["noop"] += 1
            except ValueError as error:
                skip(issue_id, str(error), "term")
            continue

        allowed = DECISIONS_BY_TYPE["ocr_block"]
        if decision not in allowed:
            skip(issue_id, f"unknown OCR decision {decision!r}", "ocr")
            continue
        if decision == "defer":
            report["ocr"]["defer"] += 1
            skip(issue_id, "deferred")
            continue

        report["ocr"][decision] += 1
        try:
            notice, image_index, block_index = _ocr_locator(issue_id)
        except ValueError as error:
            skip(issue_id, str(error), "ocr")
            continue
        current = _ocr_block_text(base, notice, image_index, block_index)
        if current is None:
            stale(issue_id, notice, image_index, block_index, None, None)
            continue
        feed = archive.feed_id(notice)
        ledger = ledgers.get(feed)
        entry = _find_correction(ledger, image_index, block_index)
        expected = (
            str(issue.get("original") or "")
            if issue is not None
            else str((entry or {}).get("original") or current)
        )
        if expected != current:
            stale(issue_id, notice, image_index, block_index, expected, current)
            continue

        if decision in ("accept", "source_issue"):
            # Both keep the ledger untouched: accept ratifies the existing
            # correction, source_issue ratifies the printed reading.
            report["noop"] += 1
            report["ocr"]["noop"] += 1
            continue

        if decision == "keep_original":
            if ledger is None:
                report["noop"] += 1
                report["ocr"]["noop"] += 1
                continue
            removed = _remove_corrections(ledger, image_index, block_index)
            if removed:
                dirty.add(feed)
                report["applied"] += 1
                report["ocr"]["applied"] += 1
            else:
                report["noop"] += 1
                report["ocr"]["noop"] += 1
            continue

        if decision == "override":
            text = record.get("corrected")
            if not isinstance(text, str) or not text:
                skip(issue_id, "override without corrected text", "ocr")
                continue
            if entry is not None and not entry.get("drop") and entry.get("corrected") == text:
                report["noop"] += 1
                report["ocr"]["noop"] += 1
                continue
            if text == current:
                # The expert chose the OCR reading itself: keep the ledger
                # valid by removing the correction instead of storing a no-op
                # entry (``corrected`` must differ from ``original``).
                removed = (
                    _remove_corrections(ledger, image_index, block_index)
                    if ledger is not None
                    else 0
                )
                if removed:
                    dirty.add(feed)
                    report["applied"] += 1
                    report["ocr"]["applied"] += 1
                else:
                    report["noop"] += 1
                    report["ocr"]["noop"] += 1
                continue
            if ledger is None:
                ledger = _new_ledger(feed)
                ledgers[feed] = ledger
            if entry is None:
                entry = _correction_base(
                    notice, image_index, block_index, current, record.get("note") or "",
                    issue_id,
                )
                entry["corrected"] = text
                ledger.setdefault("corrections", []).append(entry)
            else:
                entry["original"] = current
                entry["corrected"] = text
                entry["confidence"] = "high"
                entry["via"] = "expert-review"
                entry["reason"] = _review_reason(entry.get("reason") or "")
                entry["review_issue_id"] = issue_id
                entry.pop("drop", None)
            dirty.add(feed)
            report["applied"] += 1
            report["ocr"]["applied"] += 1
            continue

        if decision == "drop":
            if entry is not None and entry.get("drop"):
                report["noop"] += 1
                report["ocr"]["noop"] += 1
                continue
            if ledger is None:
                ledger = _new_ledger(feed)
                ledgers[feed] = ledger
            if entry is None:
                entry = _correction_base(
                    notice, image_index, block_index, current,
                    record.get("note") or "重复切片/无独立源行", issue_id,
                )
                entry["drop"] = True
                ledger.setdefault("corrections", []).append(entry)
            else:
                entry["original"] = current
                entry.pop("corrected", None)
                entry["drop"] = True
                entry["confidence"] = "high"
                entry["via"] = "expert-review"
                entry["reason"] = _review_reason(entry.get("reason") or "")
                entry["review_issue_id"] = issue_id
            dirty.add(feed)
            report["applied"] += 1
            report["ocr"]["applied"] += 1
            continue

        skip(issue_id, f"unhandled OCR decision {decision!r}", "ocr")

    report["ledger_files"] = sorted(f"corrections/dashen/{feed}.json" for feed in dirty)
    if not dry_run:
        _write_terms(base, generated_at, terms, different)
        for feed in sorted(dirty):
            atomic.write_text(
                base / "corrections" / "dashen" / f"{feed}.json",
                json.dumps(ledgers[feed], ensure_ascii=False, indent=2) + "\n",
            )
        atomic.write_text(
            base / "review" / "apply-report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    return report
