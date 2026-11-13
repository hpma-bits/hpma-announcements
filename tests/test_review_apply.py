import json

from tools import review

BLOCKS = ["原一", "原二", "原三", "原四", "原五", "原六"]
NOTICE = "dashen.aaaa"


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed(root):
    _write_jsonl(
        root / "corpus/dashen/posts.jsonl",
        [
            {
                "id": "aaaa",
                "title": "【1月2日更新公告】",
                "published_at": "2026-01-01T22:00:00+08:00",
                "text": "金锻与金缎\n补获与捕获",
                "feed_url": "https://ds.163.com/feed/aaaa/",
            }
        ],
    )
    _write_jsonl(
        root / "study/labels.jsonl",
        [{"id": "aaaa", "label": "update", "notice_date": "2026-01-02"}],
    )
    ocr_path = root / "derived/ocr/dashen/aaaa.json"
    ocr_path.parent.mkdir(parents=True, exist_ok=True)
    ocr_path.write_text(
        json.dumps(
            {
                "id": NOTICE,
                "images": [
                    {
                        "index": 1,
                        "path": "assets/dashen/aaaa/01.png",
                        "blocks": [
                            {"box": [[0, i], [10, i], [10, i + 5], [0, i + 5]], "text": text}
                            for i, text in enumerate(BLOCKS)
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ledger_path = root / "corrections/dashen/aaaa.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "id": NOTICE,
                "corrections": [
                    {
                        "image_index": 1,
                        "block_index": index,
                        "original": BLOCKS[index],
                        "corrected": f"修{index}",
                        "reason": "行尾被水印覆盖",
                        "confidence": "medium",
                    }
                    for index in range(5)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _decisions(root, *, stale="原三", override_block=0):
    path = root / "review/decisions.jsonl"
    review.save_decision(
        "term:金锻|金缎",
        "same_alias",
        {"preferred": "金缎", "aliases": ["金锻"]},
        "官方两种写法都出现",
        path=path,
    )
    review.save_decision(
        "term:补获|捕获",
        "different",
        {},
        "不同词义",
        path=path,
    )
    review.save_decision(
        f"ocr:{NOTICE}:1:{override_block}",
        "override",
        {"corrected": "专家定稿"},
        "专家看过裁剪图",
        path=path,
    )
    review.save_decision(f"ocr:{NOTICE}:1:1", "accept", {}, "接受", path=path)
    review.save_decision(
        f"ocr:{NOTICE}:1:2", "override", {"corrected": "过期文本"}, "", path=path
    )
    review.save_decision(f"ocr:{NOTICE}:1:3", "keep_original", {}, "", path=path)
    review.save_decision(f"ocr:{NOTICE}:1:4", "drop", {}, "重复切片", path=path)
    return path


def _ledger(root):
    return json.loads((root / "corrections/dashen/aaaa.json").read_text(encoding="utf-8"))


def _by_block(ledger):
    return {
        correction["block_index"]: correction
        for correction in ledger["corrections"]
        if not correction.get("insert")
    }


def test_apply_requires_decisions_and_dry_run_writes_nothing(tmp_path):
    _seed(tmp_path)
    _decisions(tmp_path)
    ledger_path = tmp_path / "corrections/dashen/aaaa.json"
    before = ledger_path.read_bytes()

    report = review.apply(tmp_path, dry_run=True)

    assert report["dry_run"] is True
    assert report["applied"] == 6  # 2 term entries + 4 OCR changes
    assert report["term"] == {
        "same_alias": 1,
        "different": 1,
        "ocr_error": 0,
        "defer": 0,
        "skipped": 0,
    }
    assert report["ocr"]["applied"] == 4  # two overrides + keep + drop
    assert report["ocr"]["noop"] == 1  # accept
    assert report["stale"] == 0
    assert report["ledger_files"] == ["corrections/dashen/aaaa.json"]
    assert ledger_path.read_bytes() == before
    assert not (tmp_path / "review/terms.json").exists()
    assert not (tmp_path / "review/apply-report.json").exists()


def test_apply_override_accept_keep_original_and_drop(tmp_path):
    _seed(tmp_path)
    decisions_path = _decisions(tmp_path)
    decisions_before = decisions_path.read_bytes()
    # The third block changed upstream after the decision was recorded.
    ocr_path = tmp_path / "derived/ocr/dashen/aaaa.json"
    payload = json.loads(ocr_path.read_text(encoding="utf-8"))
    payload["images"][0]["blocks"][2]["text"] = "原三（新 OCR）"
    ocr_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = review.apply(tmp_path)

    assert report["dry_run"] is False
    assert report["ocr"]["override"] == 2
    assert report["ocr"]["accept"] == 1
    assert report["ocr"]["keep_original"] == 1
    assert report["ocr"]["drop"] == 1
    assert report["ocr"]["stale"] == 1
    assert report["ocr"]["noop"] == 1  # accept
    assert report["stale_items"] == [
        {
            "issue_id": f"ocr:{NOTICE}:1:2",
            "notice": NOTICE,
            "image_index": 1,
            "block_index": 2,
            "expected": "原三",
            "current": "原三（新 OCR）",
        }
    ]
    assert report["skipped"] == 0
    assert report["problems"] == []

    corrections = _by_block(_ledger(tmp_path))
    overridden = corrections[0]
    assert overridden["corrected"] == "专家定稿"
    assert overridden["original"] == "原一"
    assert overridden["confidence"] == "high"
    assert overridden["via"] == "expert-review"
    assert overridden["review_issue_id"] == f"ocr:{NOTICE}:1:0"
    assert review.REVIEW_REASON_MARKER in overridden["reason"]
    assert overridden["reason"].startswith("行尾被水印覆盖")

    accepted = corrections[1]
    assert accepted["corrected"] == "修1"
    assert accepted["confidence"] == "medium"
    assert "via" not in accepted

    assert corrections[2]["corrected"] == "修2"  # stale, untouched
    assert 3 not in corrections  # keep_original removed the correction
    assert corrections[4]["drop"] is True
    assert "corrected" not in corrections[4]
    assert corrections[4]["confidence"] == "high"

    terms = json.loads((tmp_path / "review/terms.json").read_text(encoding="utf-8"))
    assert terms["terms"] == [
        {
            "preferred": "金缎",
            "aliases": ["金锻"],
            "issue_id": "term:金锻|金缎",
            "note": "官方两种写法都出现",
            "decided_at": review.load_decisions(decisions_path)["term:金锻|金缎"][
                "decided_at"
            ],
        }
    ]
    assert terms["different"] == [
        {
            "terms": ["补获", "捕获"],
            "issue_id": "term:补获|捕获",
            "note": "不同词义",
            "decided_at": review.load_decisions(decisions_path)["term:补获|捕获"][
                "decided_at"
            ],
        }
    ]
    assert decisions_path.read_bytes() == decisions_before

    apply_report = json.loads(
        (tmp_path / "review/apply-report.json").read_text(encoding="utf-8")
    )
    assert apply_report["generated_at"] == report["generated_at"]
    assert apply_report["applied"] == report["applied"]
    assert apply_report["ledger_files"] == ["corrections/dashen/aaaa.json"]


def test_apply_is_idempotent(tmp_path):
    _seed(tmp_path)
    _decisions(tmp_path)

    first = review.apply(tmp_path)
    assert first["ocr"]["applied"] == 4
    after_first = (tmp_path / "corrections/dashen/aaaa.json").read_bytes()

    second = review.apply(tmp_path)

    assert (tmp_path / "corrections/dashen/aaaa.json").read_bytes() == after_first
    assert second["ocr"]["applied"] == 0
    assert second["ocr"]["noop"] == 5  # accept + override + keep_original + drop
    assert second["ledger_files"] == []


def test_apply_creates_missing_correction_from_the_expert_text(tmp_path):
    _seed(tmp_path)
    path = tmp_path / "review/decisions.jsonl"
    review.save_decision(
        f"ocr:{NOTICE}:1:5",
        "override",
        {"corrected": "新补的修正"},
        "无既有条目",
        path=path,
    )

    report = review.apply(tmp_path)

    assert report["ocr"]["applied"] == 1
    created = _by_block(_ledger(tmp_path))[5]
    assert created == {
        "image_index": 1,
        "block_index": 5,
        "original": "原六",
        "reason": f"无既有条目；{review.REVIEW_REASON_MARKER}",
        "confidence": "high",
        "via": "expert-review",
        "review_issue_id": f"ocr:{NOTICE}:1:5",
        "recrop": f"python -m tools.cli crop {NOTICE} 1 5",
        "corrected": "新补的修正",
    }


def test_apply_skips_deferred_decisions(tmp_path):
    _seed(tmp_path)
    path = tmp_path / "review/decisions.jsonl"
    review.save_decision(f"ocr:{NOTICE}:1:0", "defer", {}, "稍后", path=path)

    report = review.apply(tmp_path)

    assert report["ocr"]["defer"] == 1
    assert report["skipped"] == 1
    assert report["skipped_items"] == [
        {"issue_id": f"ocr:{NOTICE}:1:0", "reason": "deferred"}
    ]
    assert report["ledger_files"] == []
    assert _by_block(_ledger(tmp_path))[0]["corrected"] == "修0"
