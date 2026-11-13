from tools import archive, projection


def _block(left: int, top: int, right: int, bottom: int, text: str) -> dict:
    return {
        "box": [[left, top], [right, top], [right, bottom], [left, bottom]],
        "text": text,
        "score": 0.9,
    }


def _notice(blocks: list[dict]) -> dict:
    return {"id": "dashen.aaaa", "images": [{"index": 1, "blocks": blocks}]}


def test_same_line_right_before_left_is_reordered():
    notice = _notice([
        _block(500, 10, 700, 40, "右"),
        _block(100, 12, 300, 42, "左"),
    ])

    assert projection.effective_sections(notice, None) == [(1, ["左", "右"])]


def test_already_ordered_line_is_unchanged():
    notice = _notice([
        _block(100, 10, 300, 40, "左"),
        _block(500, 12, 700, 42, "右"),
    ])

    assert projection.effective_sections(notice, None) == [(1, ["左", "右"])]


def test_different_lines_keep_their_order():
    notice = _notice([
        _block(500, 10, 700, 40, "第一行右"),
        _block(100, 100, 300, 130, "第二行左"),
    ])

    assert projection.effective_sections(notice, None) == [(1, ["第一行右", "第二行左"])]


def test_insert_follows_its_anchor_when_the_line_is_reordered():
    notice = _notice([
        _block(500, 10, 700, 40, "右"),
        _block(100, 12, 300, 42, "左"),
    ])
    ledger = {
        "corrections": [
            {"insert": True, "image_index": 1, "after_block": 1, "text": "插入"},
        ]
    }

    assert projection.effective_sections(notice, ledger) == [(1, ["左", "插入", "右"])]


def test_correction_drop_and_watermark_still_apply():
    notice = _notice([
        _block(100, 10, 300, 40, "原文"),
        _block(100, 50, 300, 80, "删掉"),
        _block(100, 90, 300, 120, "大神@哈利波特魔法觉醒"),
    ])
    ledger = {
        "corrections": [
            {"image_index": 1, "block_index": 0, "original": "原文", "corrected": "改后"},
            {"image_index": 1, "block_index": 1, "original": "删掉", "drop": True},
        ]
    }

    assert projection.effective_sections(notice, ledger) == [(1, ["改后"])]


def test_committed_notice_with_a_known_inversion_is_left_to_right():
    notice = archive.load_ocr("61f009df849ee20001d74c20")
    ledger = archive.load_ledger("61f009df849ee20001d74c20")
    lines = dict(projection.effective_sections(notice, ledger))[2]

    assert lines.index("1、全新稀有度“光辉") + 1 == lines.index("“禁忌”即将上线")


def test_assign_canonical_prefers_nga_and_marks_exactly_one():
    rows = [
        {"id": "dashen.a", "source": "dashen", "notice_date": "2026-01-02", "published_at": "2026-01-01T00:00:00+08:00"},
        {"id": "dashen.b", "source": "dashen", "notice_date": "2026-01-02", "published_at": "2026-01-01T01:00:00+08:00"},
        {"id": "nga.c", "source": "nga", "notice_date": "2026-01-02", "published_at": "2026-01-01T02:00:00+08:00"},
    ]

    projection.assign_canonical(rows)

    assert [row["id"] for row in rows if row["canonical"]] == ["nga.c"]


def test_assign_canonical_picks_one_dashen_when_nga_is_absent():
    rows = [
        {"id": "dashen.b", "source": "dashen", "notice_date": "2026-01-02", "published_at": "2026-01-01T01:00:00+08:00"},
        {"id": "dashen.a", "source": "dashen", "notice_date": "2026-01-02", "published_at": "2026-01-01T00:00:00+08:00"},
    ]

    projection.assign_canonical(rows)

    assert [row["id"] for row in rows if row["canonical"]] == ["dashen.a"]


def test_assign_canonical_handles_family_and_undated_rows():
    rows = [
        {"id": "dashen.a", "source": "dashen", "family": "update", "notice_date": "2026-01-02", "published_at": "2026-01-01T00:00:00+08:00"},
        {"id": "dashen.b", "source": "dashen", "family": "version", "notice_date": "2026-01-02", "published_at": "2026-01-01T00:00:00+08:00"},
        {"id": "dashen.c", "source": "dashen", "family": "maintenance", "notice_date": None, "published_at": "2026-01-01T00:00:00+08:00"},
    ]

    projection.assign_canonical(rows)

    assert [row["id"] for row in rows if row["canonical"]] == ["dashen.a", "dashen.b"]
