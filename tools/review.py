"""Data layer for the text review desk.

The desk asks a human to settle one text question at a time. This module
builds that queue from primary data and keeps the append-only decision ledger;
it never rewrites ``corrections/``, ``edition/`` or ``dataset/``.

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


def _term_sort_key(issue: Record) -> tuple[int, int, str]:
    impact = issue.get("impact") or {}
    return (
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
    """
    base = Path(root) if root else archive.REPO_ROOT
    rows = load_fulltext_rows(base)
    terms = term_issues(rows)
    terms.sort(key=_term_sort_key)
    ocr = _ocr_issues(base, rows)
    ocr.sort(key=_ocr_sort_key)
    issues = terms + ocr

    latest = (
        load_decisions(default_decisions_path(base))
        if decisions is None
        else _index_decisions(decisions)
    )

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
# Export
# --------------------------------------------------------------------------- #


def _effective_corrected(issue: Record, record: Record) -> str:
    decision = record.get("decision")
    if decision == "override":
        return str(record.get("corrected") or "")
    if decision == "keep_original":
        return issue.get("original") or ""
    if decision == "drop":
        return ""
    return issue.get("corrected") or ""


def _term_export(issue: Record, record: Record) -> Record | None:
    if record.get("decision") != "same_alias":
        return None
    preferred = record.get("preferred")
    if not isinstance(preferred, str) or not preferred:
        raise ValueError(f"{issue['id']}: same_alias decision is missing preferred")
    aliases = record.get("aliases")
    if not isinstance(aliases, list):
        aliases = [variant["text"] for variant in issue["variants"]]
    aliases = [
        alias
        for alias in dict.fromkeys(str(alias) for alias in aliases)
        if alias and alias != preferred
    ]
    return {
        "preferred": preferred,
        "aliases": aliases,
        "issue_id": issue["id"],
        "note": record.get("note") or "",
        "decided_at": record.get("decided_at") or "",
    }


def export(root: str | Path | None = None) -> Record:
    """Write the search aliases and the OCR decision export.

    ``review/terms.json`` carries the ``same_alias`` verdicts only.
    ``review/ocr-decisions.jsonl`` carries every non-defer OCR verdict and
    checks its ``original`` against the OCR currently on disk. A mismatch is
    marked ``stale`` so it is never applied silently.
    """
    base = Path(root) if root else archive.REPO_ROOT
    decisions = load_decisions(default_decisions_path(base))
    queue = build_queue(base, decisions=decisions)
    generated_at = _now()

    terms: list[Record] = []
    ocr_records: list[Record] = []
    stale = 0
    for issue in queue["issues"]:
        record = decisions.get(issue["id"])
        if not record:
            continue
        if issue["type"] == "term_conflict":
            entry = _term_export(issue, record)
            if entry:
                terms.append(entry)
            continue
        if record.get("decision") == "defer":
            continue
        current = _ocr_block_text(
            base, issue["notice"], issue["image_index"], issue["block_index"]
        )
        applicable = current is not None and current == issue.get("original")
        if not applicable:
            stale += 1
        ocr_records.append(
            {
                "issue_id": issue["id"],
                "notice": issue["notice"],
                "image_index": issue["image_index"],
                "block_index": issue["block_index"],
                "decision": record.get("decision"),
                "original": issue.get("original") or "",
                "corrected": _effective_corrected(issue, record),
                "current_ocr": current,
                "applicable": applicable,
                "status": "applicable" if applicable else "stale",
                "note": record.get("note") or "",
                "decided_at": record.get("decided_at") or "",
            }
        )

    terms_path = base / "review" / "terms.json"
    ocr_path = base / "review" / "ocr-decisions.jsonl"
    terms_path.parent.mkdir(parents=True, exist_ok=True)
    terms_path.write_text(
        json.dumps(
            {"generated_at": generated_at, "terms": terms},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
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
        "ocr": len(ocr_records),
        "stale": stale,
        "terms_path": str(terms_path),
        "ocr_decisions_path": str(ocr_path),
    }
