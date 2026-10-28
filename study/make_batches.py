"""Slice the corpus into labeling batches.

Reads ``corpus/dashen/posts.jsonl`` and writes compact per-batch JSON arrays
under ``study/batches/`` — the input for the labeling agents. Truncation is a
transport optimization for labeling only; the corpus keeps full text.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCH_SIZE = 100
TEXT_HEAD = 400
LONG_TEXT_HEAD = 600


def compact(post: dict) -> dict:
    return {
        "id": post["id"],
        "date": post["published_at"],
        "type": post["type"],
        "title": post["title"],
        "text": (post["text"] or "")[:TEXT_HEAD],
        "long_text_head": (post["long_text"] or "")[:LONG_TEXT_HEAD],
        "n": post["image_count"],
    }


def main() -> None:
    posts = [
        json.loads(line)
        for line in (ROOT / "corpus" / "dashen" / "posts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    out_dir = ROOT / "study" / "batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for start in range(0, len(posts), BATCH_SIZE):
        chunk = posts[start : start + BATCH_SIZE]
        name = f"batch-{start // BATCH_SIZE:03d}.json"
        (out_dir / name).write_text(
            json.dumps([compact(post) for post in chunk], ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        index.append({"file": name, "count": len(chunk), "first_id": chunk[0]["id"], "last_id": chunk[-1]["id"]})
    (out_dir / "index.json").write_text(
        json.dumps({"batch_size": BATCH_SIZE, "post_count": len(posts), "batches": index}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(index)} batches, {len(posts)} posts")


if __name__ == "__main__":
    main()
