#!/usr/bin/env python3
"""Build the correction-review checkpage under ``checkpage/``.

This is a second, focused review page on top of the OCR correction ledger and
the corrected layer.  It is deliberately separate from the full OCR checkpage:
it asks two questions only.

1. Term-level: where the source images and the clean corpus disagree about a
   spelling (骆吾/驺吾, 金锻/金缎, 石墩/石礅, ...), which form should the
   archive use?
2. Item-level: do the low-confidence, watermark-reconstruction and full-line
   rewrite corrections actually match the source image?

The page is static and offline.  It reads the original images from the
checkout, the clean body text from ``corpus/dashen/posts.jsonl`` (shown as the
reference), the corrected layer from ``derived/ocr/corrected/dashen/`` and the
correction ledgers from ``derived/ocr/corrections/dashen/``.  Review state
lives in the browser localStorage; the page exports JSON for a separate apply
step.  The build is deterministic (stable ordering, no timestamps).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = Path(__file__).resolve().parent / "correction_checkpage"
OUT_DIR = Path("checkpage")
CORPUS = Path("corpus/dashen/posts.jsonl")
OCR_DIR = Path("derived/ocr/dashen")
CORRECTED_DIR = Path("derived/ocr/corrected/dashen")
LEDGER_DIR = Path("derived/ocr/corrections/dashen")
DATA_VERSION = 1

# Term decisions the user asked to review: the source-image form on the left,
# the clean-corpus / standard form on the right.
TERMS: list[dict[str, str]] = [
    {
        "id": "luowu",
        "source": "骆吾",
        "target": "驺吾",
        "label": "骆吾 → 驺吾",
        "priority": "high",
        "note": "源图 19 篇均作「骆吾」；干净 corpus 多作「驺吾」，未出现「骆吾」。",
    },
    {
        "id": "jinduan",
        "source": "金锻",
        "target": "金缎",
        "label": "金锻 → 金缎",
        "priority": "high",
        "note": "源图内部不一致：少数期数作「金锻」，多数作「金缎」；corpus 也以「金缎」为主。",
    },
    {
        "id": "shidun",
        "source": "石墩出动",
        "target": "石礅出动",
        "label": "石墩出动 → 石礅出动",
        "priority": "high",
        "note": "本轮把 11 处「石出动」修正为「石墩出动」；但 corpus 多数作「石礅出动」，仅少量「石墩出动」。源图石/土旁需人工确认。",
    },
    {
        "id": "ohuancao",
        "source": "噢幻草",
        "target": "嗅幻草",
        "label": "噢幻草 → 嗅幻草",
        "priority": "high",
        "note": "源图少数期数作「噢幻草」；corpus 与其他源图多作「嗅幻草」。",
    },
    {
        "id": "yinhui",
        "source": "银辉之匙",
        "target": "银辉之钥",
        "label": "银辉之匙 → 银辉之钥",
        "priority": "high",
        "note": "少数源图作「银辉之匙」；corpus 与其他源图多作「银辉之钥」。",
    },
    {
        "id": "zili",
        "source": "子立",
        "target": "孑立",
        "label": "子立 → 孑立",
        "priority": "high",
        "note": "源图 5 篇作「子立」，疑为官方原文错字；corpus 未出现「子立」或「孑立」，无 corpus 依据。",
    },
    {
        "id": "gedelike",
        "source": "戈德利克山谷",
        "target": "戈德里克山谷",
        "label": "戈德利克山谷 → 戈德里克山谷",
        "priority": "high",
        "note": "源图 1 处作「戈德利克山谷」，同篇另一处作「戈德里克山谷」；corpus 均作「戈德里克」。",
    },
    {
        "id": "luben",
        "source": "鲁本·海格",
        "target": "鲁伯·海格",
        "label": "鲁本·海格 → 鲁伯·海格",
        "priority": "high",
        "note": "源图 1 处作「鲁本·海格」；corpus 与其他源图均作「鲁伯·海格」。",
    },
    {
        "id": "changpao",
        "source": "长跑专卖店",
        "target": "长袍专卖店",
        "label": "长跑专卖店 → 长袍专卖店",
        "priority": "high",
        "note": "源图 1 处作「长跑专卖店」；corpus 与其他源图均作「长袍专卖店」。",
    },
    {
        "id": "yunwu",
        "source": "云雾飘渺",
        "target": "云雾缥缈",
        "label": "云雾飘渺 → 云雾缥缈",
        "priority": "medium",
        "note": "源图两种写法并存：3 篇作「云雾飘渺」，9 篇作「云雾缥缈」；corpus 未出现该词。",
    },
    {
        "id": "fenghuang",
        "source": "凤吟凰鸣",
        "target": "凰吟凤鸣",
        "label": "凤吟凰鸣 → 凰吟凤鸣",
        "priority": "medium",
        "note": "同一篇源图内两种写法并存；corpus 多作「凰吟凤鸣」。",
    },
    {
        "id": "sashuang",
        "source": "讽爽英姿",
        "target": "飒爽英姿",
        "label": "讽爽英姿 → 飒爽英姿",
        "priority": "medium",
        "note": "同一篇源图内「飒爽英姿」与「讽爽英姿」并存；corpus 未出现该词，标准写法为「飒爽英姿」。",
    },
    {
        "id": "jinfa",
        "source": "进发出",
        "target": "迸发出",
        "label": "进发出 → 迸发出",
        "priority": "medium",
        "note": "部分源图作「进发出」；corpus 作「迸发出」。",
    },
    {
        "id": "huanxin",
        "source": "换新款",
        "target": "焕新款",
        "label": "换新款 → 焕新款",
        "priority": "medium",
        "note": "源图 1 处作「换新款」；corpus 与其他源图均作「焕新款」。",
    },
    {
        "id": "dengji",
        "source": "等级上线",
        "target": "等级上限",
        "label": "等级上线 → 等级上限",
        "priority": "medium",
        "note": "源图 1 处作「等级上线」；corpus 与其他源图均作「等级上限」。",
    },
    {
        "id": "buhuo",
        "source": "补获",
        "target": "捕获",
        "label": "补获 → 捕获",
        "priority": "low",
        "note": "源图 1 处作「补获」，corpus 也有少量；corpus 多数作「捕获」。",
    },
    {
        "id": "wuxia",
        "source": "无暇信念",
        "target": "无瑕信念",
        "label": "无暇信念 → 无瑕信念",
        "priority": "low",
        "note": "源图 1 处作「无暇信念」；corpus 未出现，标准写法应为「无瑕信念」。",
    },
    {
        "id": "denglu",
        "source": "登陆",
        "target": "登录",
        "label": "登陆 → 登录",
        "priority": "low",
        "note": "源图少数作「登陆」，多数作「登录」；corpus 两种都有、以「登录」为主。属常见异体，按需统一。",
    },
]


class BuildError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def block_box(block: dict[str, Any]) -> tuple[int, int, int, int]:
    xs = [int(point[0]) for point in block["box"]]
    ys = [int(point[1]) for point in block["box"]]
    return min(xs), min(ys), max(xs), max(ys)


def same_line(block: dict[str, Any], other: dict[str, Any]) -> bool:
    _, top, _, bottom = block_box(block)
    _, otop, _, obottom = block_box(other)
    overlap = min(bottom, obottom) - max(top, otop)
    height = min(bottom - top, obottom - otop)
    return height > 0 and overlap / height >= 0.5


def union_box(blocks: list[dict[str, Any]], index: int) -> list[list[int]]:
    target = blocks[index]
    left, top, right, bottom = block_box(target)
    for block in blocks:
        if same_line(block, target):
            bl, _, br, _ = block_box(block)
            left, right = min(left, bl), max(right, br)
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def image_size_module():
    spec = importlib.util.spec_from_file_location(
        "build_checkpage", Path(__file__).resolve().parent / "build_checkpage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.image_size


def corpus_evidence(corpus_lines: list[str], term: str, limit: int = 2) -> list[str]:
    hits = []
    for line in corpus_lines:
        if term in line:
            hits.append(line.strip()[:220])
            if len(hits) >= limit:
                break
    return hits


def collect(root: Path) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    posts = {post["id"]: post for post in read_jsonl(root / CORPUS)}
    raw_notices = {path.stem: read_json(path) for path in sorted((root / OCR_DIR).glob("*.json"))}
    corrected_notices = {
        path.stem: read_json(path) for path in sorted((root / CORRECTED_DIR).glob("*.json"))
    }
    ledgers = [read_json(path) for path in sorted((root / LEDGER_DIR).glob("*.json"))]
    size_of = image_size_module()
    corpus_lines = []
    for post in posts.values():
        corpus_lines.append((post.get("title") or "") + "\n" + (post.get("text") or ""))
    corpus_blob = "\n".join(corpus_lines)

    sizes: dict[str, tuple[int, int] | None] = {}

    def image_meta(record: dict[str, Any], image: dict[str, Any]) -> tuple[int, int]:
        path = image.get("path") or ""
        if path not in sizes:
            sizes[path] = size_of(root / path) if path else None
        size = sizes[path] or (0, 0)
        return int(size[0]), int(size[1])

    notices: dict[str, dict[str, Any]] = {}

    def notice_meta(feed_id: str) -> dict[str, Any]:
        if feed_id in notices:
            return notices[feed_id]
        post = posts.get(feed_id)
        if post is None:
            raise BuildError(f"notice {feed_id} is missing from the corpus")
        notices[feed_id] = {
            "id": f"dashen.{feed_id}",
            "feedId": feed_id,
            "title": post.get("title") or "",
            "publishedAt": post.get("published_at") or "",
            "corpusText": (post.get("text") or "").strip(),
        }
        return notices[feed_id]

    def context(blocks: list[dict[str, Any]], index: int) -> dict[str, str]:
        before = blocks[index - 1]["text"] if index > 0 else ""
        after = blocks[index + 1]["text"] if index + 1 < len(blocks) else ""
        return {"before": before, "after": after}

    def block_record(
        feed_id: str,
        image: dict[str, Any],
        image_index: int,
        block_index: int,
        blocks: list[dict[str, Any]],
        corrected_block: dict[str, Any],
    ) -> dict[str, Any]:
        meta = notice_meta(feed_id)
        width, height = image_meta(raw_notices[feed_id], image)
        block = blocks[block_index]
        return {
            "noticeId": meta["id"],
            "feedId": feed_id,
            "title": meta["title"],
            "publishedAt": meta["publishedAt"],
            "imageIndex": image_index,
            "blockIndex": block_index,
            "imagePath": raw_notices[feed_id]["images"][image_index - 1]["path"],
            "width": width,
            "height": height,
            "box": union_box(blocks, block_index),
            "original": block["text"],
            "corrected": corrected_block["text"],
            "context": context(blocks, block_index),
        }

    # ---- term-level review ------------------------------------------------
    terms = []
    for spec in TERMS:
        source = spec["source"]
        target = spec["target"]
        examples = []
        seen_notices = set()
        affected = []
        for feed_id, notice in corrected_notices.items():
            raw = raw_notices.get(feed_id)
            if raw is None:
                continue
            for image_index, image in enumerate(raw.get("images", []), 1):
                corrected_image = notice["images"][image_index - 1]
                for block_index, block in enumerate(image.get("blocks", [])):
                    corrected_block = corrected_image.get("blocks", [])[block_index]
                    if source not in corrected_block.get("text", ""):
                        continue
                    affected.append(feed_id)
                    if feed_id not in seen_notices and len(examples) < 3:
                        seen_notices.add(feed_id)
                        examples.append(
                            block_record(feed_id, image, image_index, block_index, image.get("blocks", []), corrected_block)
                        )
        affected = sorted(set(affected))
        source_blocks = sum(1 for feed_id in affected for image in corrected_notices[feed_id]["images"] for block in image.get("blocks", []) if source in block.get("text", ""))
        corpus_source = corpus_blob.count(source)
        corpus_target = corpus_blob.count(target)
        if corpus_target > 0 and corpus_source == 0:
            recommendation = "corpus"
        elif corpus_source > 0 and corpus_target == 0:
            recommendation = "source"
        elif corpus_target >= 3 * max(1, corpus_source):
            recommendation = "corpus"
        elif corpus_source >= 3 * max(1, corpus_target):
            recommendation = "source"
        else:
            recommendation = "review"
        terms.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "sourceForm": source,
                "targetForm": target,
                "priority": spec["priority"],
                "note": spec["note"],
                "sourceBlocks": source_blocks,
                "sourceNotices": len(affected),
                "corpusSource": corpus_source,
                "corpusTarget": corpus_target,
                "recommendation": recommendation,
                "corpusSnippets": corpus_evidence(corpus_lines, target if corpus_target >= corpus_source else source),
                "examples": examples,
                "affected": affected,
            }
        )
    terms.sort(key=lambda item: ({"high": 0, "medium": 1, "low": 2}[item["priority"]], -item["sourceBlocks"], item["id"]))

    # ---- item-level review ------------------------------------------------
    items = []
    item_keys = set()
    for ledger in ledgers:
        feed_id = ledger["id"].removeprefix("dashen.")
        raw = raw_notices.get(feed_id)
        corrected = corrected_notices.get(feed_id)
        if raw is None or corrected is None:
            warnings.append(f"{feed_id}: corrected/raw layer missing")
            continue
        for correction in ledger.get("corrections", []):
            image_index = int(correction["image_index"])
            block_index = int(correction["block_index"])
            image = raw["images"][image_index - 1]
            corrected_image = corrected["images"][image_index - 1]
            if block_index >= len(image.get("blocks", [])):
                warnings.append(f"{feed_id} img{image_index} b{block_index}: block missing")
                continue
            key = f"{ledger['id']}|{image_index}|{block_index}"
            if key in item_keys:
                continue
            item_keys.add(key)
            record = block_record(
                feed_id,
                image,
                image_index,
                block_index,
                image.get("blocks", []),
                corrected_image.get("blocks", [])[block_index],
            )
            reason = correction.get("reason", "")
            original = correction["original"]
            changed = correction["corrected"]
            categories = ["all"]
            if correction.get("confidence") == "medium":
                categories.append("low")
            if "水印" in reason or "大神@" in original:
                categories.append("watermark")
            if abs(len(changed) - len(original)) >= 8 or "整行" in reason:
                categories.append("rewrite")
            record.update(
                {
                    "key": key,
                    "reason": reason,
                    "confidence": correction.get("confidence", ""),
                    "categories": categories,
                    "termRefs": [
                        term["id"]
                        for term in terms
                        if term["sourceForm"] in changed or term["targetForm"] in changed
                    ],
                }
            )
            items.append(record)
    items.sort(key=lambda item: (item["publishedAt"], item["noticeId"], item["imageIndex"], item["blockIndex"]))

    # Only ship notices that the page references.
    used = {item["feedId"] for item in items}
    used.update(term["affected"][0] for term in terms if term["affected"])
    used.update(example["feedId"] for term in terms for example in term["examples"])
    notice_payload = {}
    for feed_id in sorted(used):
        meta = notice_meta(feed_id)
        notice_payload[meta["id"]] = meta

    summary = {
        "terms": len(terms),
        "low": sum(1 for item in items if "low" in item["categories"]),
        "watermark": sum(1 for item in items if "watermark" in item["categories"]),
        "rewrite": sum(1 for item in items if "rewrite" in item["categories"]),
        "all": len(items),
        "notices": len({item["noticeId"] for item in items}),
    }
    payload = {
        "version": DATA_VERSION,
        "generatedFrom": "corpus/dashen/posts.jsonl + derived/ocr/corrected/dashen + derived/ocr/corrections/dashen + assets",
        "summary": summary,
        "notices": notice_payload,
        "terms": terms,
        "items": items,
    }
    return payload, warnings


def data_js(payload: dict[str, Any]) -> str:
    return (
        "window.CORRECTION_REVIEW_DATA="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )


def render_html() -> str:
    template = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    style = (TEMPLATE_DIR / "style.css").read_text(encoding="utf-8")
    app = (TEMPLATE_DIR / "app.js").read_text(encoding="utf-8")
    if "/*{{STYLE}}*/" not in template or "/*{{APP}}*/" not in template:
        raise BuildError("template is missing the /*{{STYLE}}*/ or /*{{APP}}*/ marker")
    return template.replace("/*{{STYLE}}*/", style).replace("/*{{APP}}*/", app)


def _write(path: Path, content: str) -> bool:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return True


def build(root: Path, out_dir: Path, check: bool = False) -> tuple[dict[str, Any], list[str], dict[str, bool]]:
    payload, warnings = collect(root)
    outputs = {"corrections.html": render_html(), "corrections-data.js": data_js(payload)}
    changed = {}
    for name, content in outputs.items():
        path = out_dir / name
        differs = not path.exists() or path.read_text(encoding="utf-8") != content
        changed[name] = differs
        if differs and not check:
            _write(path, content)
    return payload, warnings, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build the correction-review checkpage")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="fail if the generated files are stale")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out_dir = (args.out or (root / OUT_DIR)).resolve()
    try:
        payload, warnings, changed = build(root, out_dir, check=args.check)
    except BuildError as exc:
        print(f"correction-checkpage: {exc}", file=sys.stderr)
        return 2
    stale = [name for name, differs in changed.items() if differs]
    if not args.quiet:
        summary = payload["summary"]
        print(
            f"correction-checkpage: {summary['terms']} terms, {summary['low']} low-confidence, "
            f"{summary['watermark']} watermark, {summary['rewrite']} rewrite, {summary['all']} total"
        )
        if warnings:
            print(f"  warnings ({len(warnings)}):")
            for warning in warnings[:10]:
                print(f"    - {warning}")
        print("  stale: " + (", ".join(stale) if stale else "none") if args.check else "  wrote: " + (", ".join(stale) if stale else "nothing (up to date)"))
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
