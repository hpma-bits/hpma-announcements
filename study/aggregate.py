"""Aggregate per-batch label files into the consolidated label ledger.

Validates that every corpus post is labeled exactly once, writes
``study/labels.jsonl`` (corpus order), ``study/positives.json`` (the
synthesis agent's input: every non-`event`/`other` label with fuller text),
and ``study/labels-manifest.json`` (prompt version, counts, coverage).
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNTHESIS_LABELS = {"update", "maintenance", "version", "test_server", "ambiguous"}
TEXT_HEAD = 600
LONG_TEXT_HEAD = 1200

VALID_LABELS = {"update", "maintenance", "version", "test_server", "event", "other", "ambiguous"}


def main() -> int:
    posts = [
        json.loads(line)
        for line in (ROOT / "corpus" / "dashen" / "posts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels: dict[str, dict] = {}
    problems: list[str] = []
    label_files = sorted((ROOT / "study" / "labels").glob("batch-*.jsonl"))
    for path in label_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{path.name}:{lineno}: bad JSON ({exc})")
                continue
            post_id = record.get("id")
            label = record.get("label")
            if label not in VALID_LABELS:
                problems.append(f"{path.name}:{lineno}: invalid label {label!r}")
                continue
            if post_id in labels:
                problems.append(f"{path.name}:{lineno}: duplicate label for {post_id}")
                continue
            labels[post_id] = {
                "id": post_id,
                "label": label,
                "confidence": record.get("confidence"),
                "evidence": record.get("evidence"),
                "notice_date": record.get("notice_date"),
            }

    missing = [post["id"] for post in posts if post["id"] not in labels]
    unknown = sorted(set(labels) - {post["id"] for post in posts})
    problems.extend(f"unknown post id {post_id}" for post_id in unknown)
    ledger = [labels[post["id"]] for post in posts if post["id"] in labels]
    (ROOT / "study" / "labels.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in ledger),
        encoding="utf-8",
        newline="\n",
    )

    by_id = {post["id"]: post for post in posts}
    positives = []
    for post in posts:
        row = labels.get(post["id"])
        if not row or row["label"] not in SYNTHESIS_LABELS:
            continue
        positives.append(
            {
                "id": post["id"],
                "label": row["label"],
                "confidence": row["confidence"],
                "evidence": row["evidence"],
                "notice_date": row["notice_date"],
                "date": post["published_at"],
                "type": post["type"],
                "title": post["title"],
                "text": (post["text"] or "")[:TEXT_HEAD],
                "long_text_head": (post["long_text"] or "")[:LONG_TEXT_HEAD],
                "n": post["image_count"],
            }
        )
    (ROOT / "study" / "positives.json").write_text(
        json.dumps(positives, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    prompt = ROOT / "study" / "prompt-label.md"
    manifest = {
        "prompt_version": 1,
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        "labeler": "zcode session subagents",
        "batches": len(label_files),
        "labeled": len(labels),
        "post_count": len(posts),
        "missing": len(missing),
        "counts_by_label": dict(Counter(row["label"] for row in ledger)),
        "counts_by_confidence": dict(Counter(row["confidence"] for row in ledger)),
        "problems": problems[:50],
    }
    (ROOT / "study" / "labels-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "problems"}, ensure_ascii=False, indent=2))
    for problem in manifest["problems"]:
        print("PROBLEM", problem, file=sys.stderr)
    if missing:
        print(f"MISSING {len(missing)}: {missing[:20]}", file=sys.stderr)
    return 0 if not missing and not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
