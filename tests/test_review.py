import json

import pytest

from tools import review

ROW_FIELDS = {
    "id",
    "source",
    "title",
    "notice_date",
    "published_at",
    "url",
    "body",
    "image_text",
    "sections",
    "images",
    "canonical",
}


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
                "text": "开头\n金锻与金缎\n结尾",
                "feed_url": "https://ds.163.com/feed/aaaa/",
            },
            {
                "id": "bbbb",
                "title": "【1月3日更新公告】",
                "published_at": "2026-01-02T22:00:00+08:00",
                "text": "金缎 金缎\n金锻 金锻\n金缎\n登陆 登陆 登陆 登录",
                "feed_url": "https://ds.163.com/feed/bbbb/",
            },
        ],
    )
    _write_jsonl(
        root / "study/labels.jsonl",
        [
            {"id": "aaaa", "label": "update", "notice_date": "2026-01-02"},
            {"id": "bbbb", "label": "update", "notice_date": "2026-01-03"},
        ],
    )
    (root / "derived/ocr/dashen").mkdir(parents=True)
    (root / "derived/ocr/dashen/aaaa.json").write_text(
        json.dumps(
            {
                "id": "dashen.aaaa",
                "images": [
                    {
                        "index": 1,
                        "path": "assets/dashen/aaaa/01.png",
                        "blocks": [
                            {"box": [[10, 10], [90, 10], [90, 40], [10, 40]], "text": "图片第一行"},
                            {"box": [[10, 50], [90, 50], [90, 80], [10, 80]], "text": "图片第二行"},
                            {
                                "box": [[10, 90], [90, 90], [90, 120], [10, 120]],
                                "text": "图片第三行 金缎",
                            },
                            {
                                "box": [[10, 130], [90, 130], [90, 160], [10, 160]],
                                "text": "图片第四行",
                            },
                            {
                                "box": [[10, 170], [90, 170], [90, 200], [10, 200]],
                                "text": "图片第五行",
                            },
                            {
                                "box": [[10, 210], [90, 210], [90, 240], [10, 240]],
                                "text": "图片第六行",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "derived/ocr/dashen/bbbb.json").write_text(
        json.dumps({"id": "dashen.bbbb", "images": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "corrections/dashen").mkdir(parents=True)
    (root / "corrections/dashen/aaaa.json").write_text(
        json.dumps(
            {
                "id": "dashen.aaaa",
                "corrections": [
                    {
                        "image_index": 1,
                        "block_index": 0,
                        "original": "图片第一行",
                        "corrected": "XY",
                        "reason": "普通修正",
                        "confidence": "medium",
                    },
                    {
                        "image_index": 1,
                        "block_index": 1,
                        "original": "图片第二行",
                        "corrected": "图片第二行（修正）",
                        "reason": "页脚装饰水印 HARRY POTTER letter-spaced",
                        "confidence": "medium",
                    },
                    {
                        "image_index": 1,
                        "block_index": 2,
                        "original": "图片第三行 金缎",
                        "corrected": "图片第三行 金缎（修正）",
                        "reason": "行尾被水印覆盖",
                        "confidence": "medium",
                    },
                    {
                        "image_index": 1,
                        "block_index": 3,
                        "original": "图片第四行",
                        "corrected": "图片第四行（高置信）",
                        "reason": "高置信修正",
                        "confidence": "high",
                    },
                    {
                        "image_index": 1,
                        "block_index": 4,
                        "original": "图片第五行",
                        "corrected": "",
                        "drop": True,
                        "reason": "重复行",
                        "confidence": "medium",
                    },
                    {
                        "insert": True,
                        "image_index": 1,
                        "after_block": 0,
                        "text": "插入行",
                        "reason": "漏行",
                        "confidence": "medium",
                    },
                    {
                        "image_index": 1,
                        "block_index": 5,
                        "original": "图片第六行",
                        "corrected": "修正六",
                        "reason": "普通修正",
                        "confidence": "medium",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_load_fulltext_rows(tmp_path):
    _seed(tmp_path)
    rows = review.load_fulltext_rows(tmp_path)

    assert [row["id"] for row in rows] == ["dashen.aaaa", "dashen.bbbb"]
    assert set(rows[0]) == ROW_FIELDS
    assert rows[0]["source"] == "dashen"
    assert rows[0]["notice_date"] == "2026-01-02"
    assert rows[0]["url"] == "https://ds.163.com/feed/aaaa/"
    assert "图片第三行 金缎（修正）" in rows[0]["image_text"]
    assert rows[0]["sections"][0]["image"] == 1
    assert rows[0]["images"] == ["assets/dashen/aaaa/01.png"]
    assert rows[1]["body"].startswith("金缎 金缎")
    assert all(row["canonical"] for row in rows)


def test_load_fulltext_rows_tolerates_stale_ledger(tmp_path):
    _seed(tmp_path)
    ocr_path = tmp_path / "derived/ocr/dashen/aaaa.json"
    payload = json.loads(ocr_path.read_text(encoding="utf-8"))
    payload["images"][0]["blocks"][2]["text"] = "图片第三行 金缎（新 OCR）"
    ocr_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rows = review.load_fulltext_rows(tmp_path)

    assert "图片第三行 金缎（新 OCR）" in rows[0]["image_text"]


def test_term_issues_counts_contexts_and_severity(tmp_path):
    _seed(tmp_path)
    issues = review.term_issues(review.load_fulltext_rows(tmp_path))
    by_id = {issue["id"]: issue for issue in issues}

    assert list(by_id) == ["term:金锻|金缎", "term:登陆|登录"]
    gold = by_id["term:金锻|金缎"]
    assert gold["type"] == "term_conflict"
    assert gold["severity"] == "high"
    assert gold["variants"] == [
        {"text": "金锻", "count": 3, "notices": 2},
        {"text": "金缎", "count": 5, "notices": 2},
    ]
    assert gold["impact"] == {"minor_count": 3, "total_notices": 2, "search_gain": 3}
    assert [option["value"] for option in gold["options"]] == [
        "same_alias",
        "different",
        "ocr_error",
        "defer",
    ]
    assert "仅供参考" in gold["recommendation"]

    first = next(context for context in gold["contexts"] if context["variant"] == "金锻")
    assert first == {
        "variant": "金锻",
        "notice_id": "dashen.aaaa",
        "notice_date": "2026-01-02",
        "title": "【1月2日更新公告】",
        "source": "dashen",
        "section": "正文",
        "image": None,
        "line": "金锻与金缎",
        "before": "开头",
        "after": "结尾",
    }
    assert any(context["section"] == "图片文字" for context in gold["contexts"])

    login = by_id["term:登陆|登录"]
    assert login["severity"] == "medium"
    assert login["variants"] == [
        {"text": "登陆", "count": 3, "notices": 1},
        {"text": "登录", "count": 1, "notices": 1},
    ]


def test_term_issues_caps_contexts_but_keeps_total_count():
    rows = [
        {
            "id": "dashen.xxxx",
            "source": "dashen",
            "title": "标题",
            "notice_date": "2026-01-01",
            "body": "\n".join(["金锻"] * 10 + ["金缎"]),
            "image_text": "",
            "sections": [],
        }
    ]
    issues = review.term_issues(rows)

    assert len(issues) == 1
    assert issues[0]["variants"][0]["count"] == 10
    assert len(issues[0]["contexts"]) == 1 + review.MAX_CONTEXTS_PER_VARIANT


def test_term_issues_skip_pairs_with_only_one_spelling():
    rows = [
        {
            "id": "dashen.xxxx",
            "source": "dashen",
            "title": "标题",
            "notice_date": "2026-01-01",
            "body": "只有金锻",
            "image_text": "",
            "sections": [],
        }
    ]

    assert review.term_issues(rows) == []


def test_ocr_issues_filters_and_context(tmp_path):
    _seed(tmp_path)
    issues = review.ocr_issues(tmp_path)
    by_id = {issue["id"]: issue for issue in issues}

    assert list(by_id) == [
        "ocr:dashen.aaaa:1:0",
        "ocr:dashen.aaaa:1:2",
        "ocr:dashen.aaaa:1:5",
    ]
    blocked = by_id["ocr:dashen.aaaa:1:2"]
    assert blocked["type"] == "ocr_block"
    assert blocked["severity"] == "high"
    assert blocked["notice"] == "dashen.aaaa"
    assert blocked["notice_date"] == "2026-01-02"
    assert blocked["notice_title"] == "【1月2日更新公告】"
    assert blocked["original"] == "图片第三行 金缎"
    assert blocked["corrected"] == "图片第三行 金缎（修正）"
    assert blocked["reason"] == "行尾被水印覆盖"
    assert blocked["confidence"] == "medium"
    assert blocked["crop"] == {
        "notice": "dashen.aaaa",
        "image": 1,
        "block": 2,
        "scale": 2,
    }
    assert blocked["context_before"] == ["图片第一行", "图片第二行"]
    assert blocked["context_after"] == ["图片第四行", "图片第五行"]
    assert [option["value"] for option in blocked["options"]] == [
        "accept",
        "override",
        "keep_original",
        "source_issue",
        "drop",
        "defer",
    ]

    first = by_id["ocr:dashen.aaaa:1:0"]
    assert first["severity"] == "medium"
    assert first["context_before"] == []
    assert first["context_after"] == ["图片第二行", "图片第三行 金缎"]

    fifth = by_id["ocr:dashen.aaaa:1:5"]
    assert fifth["context_before"] == ["图片第四行", "图片第五行"]
    assert fifth["context_after"] == []


def test_build_queue_sorts_and_counts(tmp_path):
    _seed(tmp_path)
    queue = review.build_queue(tmp_path)

    assert [issue["id"] for issue in queue["issues"]] == [
        "term:金锻|金缎",
        "ocr:dashen.aaaa:1:2",
        "term:登陆|登录",
        "ocr:dashen.aaaa:1:0",
        "ocr:dashen.aaaa:1:5",
    ]
    assert queue["stats"] == {
        "total": 5,
        "pending": 5,
        "resolved": 0,
        "deferred": 0,
        "high": 2,
        "high_total": 2,
        "term": 2,
        "ocr": 3,
        "term_pending": 2,
        "ocr_pending": 3,
    }

    decisions = [
        {
            "issue_id": "term:金锻|金缎",
            "type": "term_conflict",
            "decision": "different",
        },
        {"issue_id": "ocr:dashen.aaaa:1:2", "type": "ocr_block", "decision": "defer"},
    ]
    queue = review.build_queue(tmp_path, decisions=decisions)
    assert queue["stats"]["resolved"] == 1
    assert queue["stats"]["deferred"] == 1
    assert queue["stats"]["pending"] == 4
    assert queue["stats"]["high"] == 1


def test_load_and_save_decisions_latest_wins(tmp_path):
    path = tmp_path / "review/decisions.jsonl"
    assert review.load_decisions(path) == {}

    first = review.save_decision(
        "term:金锻|金缎",
        "same_alias",
        {"preferred": "金缎", "aliases": ["金锻"]},
        "第一次",
        path=path,
    )
    second = review.save_decision(
        "term:金锻|金缎",
        "different",
        {},
        "改判",
        path=path,
    )
    assert first["type"] == "term_conflict"
    assert first["decided_at"]
    assert second["decided_at"]

    latest = review.load_decisions(path)
    assert latest["term:金锻|金缎"]["decision"] == "different"
    assert latest["term:金锻|金缎"]["note"] == "改判"


def test_save_decision_validates(tmp_path):
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError):
        review.save_decision("term:金锻|金缎", "accept", {}, path=path)
    with pytest.raises(ValueError):
        review.save_decision("term:金锻|金缎", "same_alias", {}, path=path)
    with pytest.raises(ValueError):
        review.save_decision("ocr:dashen.aaaa:1:2", "override", {}, path=path)
    with pytest.raises(ValueError):
        review.save_decision("unknown:id", "defer", {}, path=path)

    review.save_decision("ocr:dashen.aaaa:1:2", "defer", {}, path=path)
    assert review.load_decisions(path)["ocr:dashen.aaaa:1:2"]["decision"] == "defer"


def test_load_decisions_rejects_malformed(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        review.load_decisions(path)


def test_export_writes_terms_and_ocr_decisions(tmp_path):
    _seed(tmp_path)
    ledger = tmp_path / "corrections/dashen/aaaa.json"
    before = ledger.read_text(encoding="utf-8")
    path = tmp_path / "review/decisions.jsonl"

    review.save_decision(
        "term:金锻|金缎",
        "same_alias",
        {"preferred": "金缎", "aliases": ["金锻"]},
        "官方两种写法都出现",
        path=path,
    )
    review.save_decision(
        "term:登陆|登录",
        "different",
        {},
        "不同事物",
        path=path,
    )
    review.save_decision("ocr:dashen.aaaa:1:2", "accept", {}, "接受修正", path=path)
    review.save_decision(
        "ocr:dashen.aaaa:1:0",
        "override",
        {"corrected": "专家定稿"},
        "专家输入",
        path=path,
    )
    review.save_decision("ocr:dashen.aaaa:1:5", "defer", {}, "稍后", path=path)

    summary = review.export(tmp_path)

    assert summary["terms"] == 1
    assert summary["ocr"] == 2
    assert summary["stale"] == 0
    assert summary["generated_at"]

    terms = json.loads((tmp_path / "review/terms.json").read_text(encoding="utf-8"))
    assert terms["generated_at"] == summary["generated_at"]
    assert terms["terms"] == [
        {
            "preferred": "金缎",
            "aliases": ["金锻"],
            "issue_id": "term:金锻|金缎",
            "note": "官方两种写法都出现",
            "decided_at": review.load_decisions(path)["term:金锻|金缎"]["decided_at"],
        }
    ]

    lines = (tmp_path / "review/ocr-decisions.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["issue_id"] for record in records] == [
        "ocr:dashen.aaaa:1:2",
        "ocr:dashen.aaaa:1:0",
    ]
    accepted, overridden = records
    assert accepted["decision"] == "accept"
    assert accepted["corrected"] == "图片第三行 金缎（修正）"
    assert accepted["original"] == "图片第三行 金缎"
    assert accepted["current_ocr"] == "图片第三行 金缎"
    assert accepted["applicable"] is True
    assert accepted["status"] == "applicable"
    assert overridden["decision"] == "override"
    assert overridden["corrected"] == "专家定稿"
    assert overridden["status"] == "applicable"

    assert ledger.read_text(encoding="utf-8") == before
    assert not (tmp_path / "edition").exists()
    assert not (tmp_path / "dataset").exists()
    assert (tmp_path / "review/decisions.jsonl").is_file()


def test_export_marks_changed_ocr_stale(tmp_path):
    _seed(tmp_path)
    path = tmp_path / "review/decisions.jsonl"
    review.save_decision("ocr:dashen.aaaa:1:2", "accept", {}, path=path)

    first = review.export(tmp_path)
    assert first["stale"] == 0

    ocr_path = tmp_path / "derived/ocr/dashen/aaaa.json"
    payload = json.loads(ocr_path.read_text(encoding="utf-8"))
    payload["images"][0]["blocks"][2]["text"] = "图片第三行 金缎（新 OCR）"
    ocr_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    second = review.export(tmp_path)

    assert second["stale"] == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "review/ocr-decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 1
    assert records[0]["status"] == "stale"
    assert records[0]["applicable"] is False
    assert records[0]["current_ocr"] == "图片第三行 金缎（新 OCR）"


def test_export_without_decisions_writes_empty_files(tmp_path):
    _seed(tmp_path)
    summary = review.export(tmp_path)

    assert summary["terms"] == 0
    assert summary["ocr"] == 0
    assert json.loads((tmp_path / "review/terms.json").read_text(encoding="utf-8"))[
        "terms"
    ] == []
    assert (tmp_path / "review/ocr-decisions.jsonl").read_text(encoding="utf-8") == ""
