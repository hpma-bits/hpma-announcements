import json

import pytest

from tools import archive


def notice() -> dict:
    return {
        "id": "dashen.aaaa",
        "images": [
            {
                "index": 1,
                "path": "assets/dashen/aaaa/01.png",
                "text": "魔祛觉醒\n维护更新公告",
                "blocks": [
                    {"box": [[10, 10], [90, 10], [90, 40], [10, 40]], "text": "魔祛觉醒", "score": 0.9},
                    {"box": [[10, 50], [90, 50], [90, 80], [10, 80]], "text": "维护更新公告", "score": 0.99},
                ],
            }
        ],
    }


def ledger() -> dict:
    return {
        "id": "dashen.aaaa",
        "corrections": [
            {
                "image_index": 1,
                "block_index": 0,
                "original": "魔祛觉醒",
                "corrected": "魔法觉醒",
                "reason": "祛→法",
                "confidence": "high",
            }
        ],
    }


def test_feed_id_accepts_both_forms():
    assert archive.feed_id("dashen.aaaa") == "aaaa"
    assert archive.feed_id("aaaa") == "aaaa"


def test_check_ledger_accepts_matching_original():
    assert archive.check_ledger(notice(), ledger()) == []


def test_check_ledger_rejects_stale_original():
    stale = ledger()
    stale["corrections"][0]["original"] = "魔祛觉配"
    problems = archive.check_ledger(notice(), stale)
    assert any("original mismatch" in problem for problem in problems)


def test_check_ledger_rejects_duplicate_locator():
    duplicate = ledger()
    duplicate["corrections"].append(dict(duplicate["corrections"][0]))
    problems = archive.check_ledger(notice(), duplicate)
    assert any("duplicate locator" in problem for problem in problems)


def test_check_ledger_rejects_out_of_range_and_noop():
    broken = ledger()
    broken["corrections"].append(
        {"image_index": 9, "block_index": 0, "original": "x", "corrected": "y"}
    )
    broken["corrections"].append(
        {"image_index": 1, "block_index": 1, "original": "维护更新公告", "corrected": "维护更新公告"}
    )
    problems = archive.check_ledger(notice(), broken)
    assert any("out of range" in problem for problem in problems)
    assert any("corrected equals original" in problem for problem in problems)


def test_corrected_blocks_maps_locator_to_reading():
    assert archive.corrected_blocks(notice(), ledger()) == {(1, 0): "魔法觉醒"}


def test_corrected_blocks_without_ledger_is_empty():
    assert archive.corrected_blocks(notice(), None) == {}


def test_corrected_blocks_refuses_stale_ledger():
    stale = ledger()
    stale["corrections"][0]["original"] = "魔祛觉配"
    with pytest.raises(ValueError, match="original mismatch"):
        archive.corrected_blocks(notice(), stale)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def test_selected_posts_filters_labels_and_sorts_by_publication(tmp_path):
    _write_jsonl(
        tmp_path / "corpus/dashen/posts.jsonl",
        [
            {"id": "cccc", "published_at": "2022-03-01T00:00:00+08:00", "title": "C"},
            {"id": "aaaa", "published_at": "2022-01-01T00:00:00+08:00", "title": "A"},
            {"id": "bbbb", "published_at": "2022-02-01T00:00:00+08:00", "title": "B"},
        ],
    )
    _write_jsonl(
        tmp_path / "study/labels.jsonl",
        [
            {"id": "aaaa", "label": "update"},
            {"id": "bbbb", "label": "event"},
            {"id": "cccc", "label": "update"},
        ],
    )

    chosen = archive.selected_posts(tmp_path)

    assert [post["id"] for post, _ in chosen] == ["aaaa", "cccc"]
    assert all(row["label"] == "update" for _, row in chosen)
