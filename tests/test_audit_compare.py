import pytest

from tools import audit_compare as ac


def test_normalize_keeps_cjk_and_folds_width():
    assert ac.normalize("活动时间：８月１日 08:00") == "活动时间8月1日0800"
    assert ac.normalize("“银辉之匙”*16") == "银辉之匙16"
    assert ac.normalize("") == ""


def test_is_watermark_covers_suffix_and_bare_fragments():
    assert ac.is_watermark("大神@哈利波特魔法觉醒")
    assert ac.is_watermark("大神 @哈利波特魔法觉醒")
    assert ac.is_watermark("大神@哈利波属法觉醒")
    assert ac.is_watermark("魔法觉醒")
    assert not ac.is_watermark("《哈利波特：魔法觉醒》工作室")
    assert not ac.is_watermark("正文结尾 大神@哈利波特魔法觉醒")


def _blocks(texts):
    return [
        {"box": [[10, 10 + 40 * i], [90, 10 + 40 * i], [90, 35 + 40 * i], [10, 35 + 40 * i]],
         "text": text, "score": 0.9}
        for i, text in enumerate(texts)
    ]


def _notice(texts, index=1):
    return {"id": "dashen.aaaa", "images": [{"index": index, "blocks": _blocks(texts)}]}


def test_align_texts_pairs_equal_lines():
    ops = ac.align_texts([(0, "第一行"), (1, "第二行")], [(0, "第一行"), (1, "第二行")])
    assert [(op["v4"], op["v6"], op["relation"]) for op in ops] == [
        (0, 0, "equal"), (1, 1, "equal"),
    ]


def test_align_texts_rescues_a_merge():
    v4 = [(0, "1、全新稀有度“光辉”"), (1, "“禁忌”即将上线"), (2, "下一行")]
    v6 = [(0, "1、全新稀有度“光辉”“禁忌”即将上线"), (1, "下一行")]
    ops = ac.align_texts(v4, v6)
    assert [(op["v4"], op["v6"], op["relation"]) for op in ops] == [
        (0, 0, "merged"), (1, 0, "merged"), (2, 1, "equal"),
    ]


def test_align_texts_rescues_a_split():
    v4 = [(0, "活动时间：8月10日维护后"), (1, "下一行")]
    v6 = [(0, "活动时间："), (1, "8月10日维护后"), (2, "下一行")]
    ops = ac.align_texts(v4, v6)
    assert [(op["v4"], op["v6"], op["relation"]) for op in ops] == [
        (0, 0, "split"), (0, 1, "split"), (1, 2, "equal"),
    ]


def test_align_texts_flags_real_differences():
    ops = ac.align_texts([(0, "稀有度由“传说”调整为“禁忌”")], [(0, "稀有麻中“传说”调整为“禁已”")])
    assert len(ops) == 1
    assert ops[0]["relation"] == "differ"
    assert ops[0]["v4_text"] == "稀有度由“传说”调整为“禁忌”"


def test_align_texts_reports_unmatched_sides():
    ops = ac.align_texts([(0, "只有v4有"), (1, "v4的第二行")], [(0, "只有v6有")])
    kinds = {op["relation"] for op in ops}
    assert kinds == {"differ", "v4_only"}


def test_align_texts_residual_match_rescues_scrambled_equal_blocks():
    ops = ac.align_texts(
        [(0, "甲行内容"), (1, "乙行内容"), (2, "丙行内容")],
        [(0, "丙行内容"), (1, "甲行内容"), (2, "乙行内容")],
    )
    assert {op["relation"] for op in ops} == {"equal"}


def test_compare_notice_classifies_counts_and_drops():
    v4 = _notice(["维护更新公告", "第一段正文内容", "大神@哈利波特魔法觉醒", "重复残片"])
    ledger = {
        "id": "dashen.aaaa",
        "corrections": [
            {"image_index": 1, "block_index": 3, "original": "重复残片", "drop": True}
        ],
    }
    v6 = _notice(["维护更新公告", "第一段正文的内容", "大神 @哈利波特魔法觉醒"])
    result = ac.compare_notice(v4, ledger, v6)
    counts = result["counts"]
    assert counts["equal"] == 1
    assert counts["differ"] == 1
    assert counts["watermark"] == 2  # one watermark block per engine reading
    assert counts["dropped"] == 1


def test_compare_notice_marks_punctuation_only_drift():
    v4 = _notice(["将于6月1日04:00-09:00维护"])
    v6 = _notice(["将于6月1日04：00-09:00维护"])
    result = ac.compare_notice(v4, None, v6)
    assert result["counts"]["punct"] == 1
    op = result["images"][0]["ops"][0]
    assert op["v4_text"] == "将于6月1日04:00-09:00维护"
    assert op["v6_text"] == "将于6月1日04：00-09:00维护"


def test_compare_notice_reports_missing_v6_image():
    v4 = _notice(["第一行"])
    v6 = {"id": "dashen.aaaa", "images": []}
    result = ac.compare_notice(v4, None, v6)
    assert result["images"][0].get("error") == "no v6 reading for this image"
