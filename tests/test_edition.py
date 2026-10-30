import json
from pathlib import Path

import pytest

from tools import archive, edition

WATERMARK = "大神@哈利波特魔法觉醒"


def _notice() -> dict:
    return {
        "id": "dashen.aaaa",
        "images": [
            {
                "index": 1,
                "path": "assets/dashen/aaaa/01.png",
                "blocks": [
                    {"box": [[10, 10], [90, 10], [90, 40], [10, 40]], "text": "第一行", "score": 0.9},
                    {"box": [[10, 50], [90, 50], [90, 80], [10, 80]], "text": "第一行", "score": 0.9},
                    {"box": [[10, 90], [90, 90], [90, 120], [10, 120]], "text": f"正文结尾 {WATERMARK}", "score": 0.8},
                    {"box": [[10, 130], [90, 130], [90, 160], [10, 160]], "text": WATERMARK, "score": 0.7},
                    {"box": [[10, 170], [90, 170], [90, 200], [10, 200]], "text": "魔法觉醒", "score": 0.7},
                    {"box": [[10, 210], [90, 210], [90, 240], [10, 240]], "text": "第二行", "score": 0.9},
                ],
            }
        ],
    }


def _post() -> dict:
    return {
        "id": "aaaa",
        "published_at": "2026-01-01T00:00:00+08:00",
        "title": "【1月1日更新公告】",
        "text": "维护窗口\r\n第二段  ",
        "feed_url": "https://ds.163.com/feed/aaaa/",
    }


def _selection() -> dict:
    return {"id": "aaaa", "label": "update", "notice_date": "2026-01-02"}


def _render(ledger=None) -> str:
    return edition.render_notice(_post(), _selection(), _notice(), ledger)


def test_frontmatter_carries_the_provenance():
    text = _render()
    head = text.split("---")[1]

    assert 'id: "dashen.aaaa"' in head
    assert 'title: "【1月1日更新公告】"' in head
    assert 'published_at: "2026-01-01T00:00:00+08:00"' in head
    assert 'notice_date: "2026-01-02"' in head
    assert 'url: "https://ds.163.com/feed/aaaa/"' in head
    assert '  - "assets/dashen/aaaa/01.png"' in head


def test_body_text_is_normalised():
    body = _render().split("## 正文")[1].split("## 图片文字")[0]
    assert body.strip() == "维护窗口\n第二段"


def test_each_image_gets_a_section_of_its_own():
    text = _render()
    assert "## 图片文字" in text
    assert "### 图 1" in text


def test_overlapping_tiles_keep_one_copy_of_a_line():
    assert _render().count("第一行") == 1


def test_watermark_blocks_are_dropped():
    text = _render()
    assert WATERMARK not in text
    assert "魔法觉醒" not in text


def test_watermark_suffix_is_cut_from_a_content_line():
    assert "正文结尾" in _render()


def test_corrections_replace_the_engine_reading():
    ledger = {
        "id": "dashen.aaaa",
        "corrections": [
            {
                "image_index": 1,
                "block_index": 5,
                "original": "第二行",
                "corrected": "改过的行",
                "reason": "测试",
                "confidence": "high",
            }
        ],
    }
    text = _render(ledger)

    assert "改过的行" in text
    assert "第二行" not in text


def test_stale_correction_is_refused():
    ledger = {
        "id": "dashen.aaaa",
        "corrections": [
            {
                "image_index": 1,
                "block_index": 5,
                "original": "已经变了",
                "corrected": "改过的行",
            }
        ],
    }
    with pytest.raises(ValueError, match="original mismatch"):
        _render(ledger)


def _seed(root: Path) -> None:
    (root / "corpus/dashen").mkdir(parents=True)
    (root / "study").mkdir()
    (root / "derived/ocr/dashen").mkdir(parents=True)
    (root / "corpus/dashen/posts.jsonl").write_text(
        json.dumps(_post(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "study/labels.jsonl").write_text(
        json.dumps(_selection(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "derived/ocr/dashen/aaaa.json").write_text(
        json.dumps(_notice(), ensure_ascii=False), encoding="utf-8"
    )


def test_build_is_deterministic(tmp_path):
    _seed(tmp_path)

    assert edition.build(tmp_path) == edition.build(tmp_path)


def test_build_raises_when_a_ledger_is_stale(tmp_path):
    _seed(tmp_path)
    (tmp_path / "derived/ocr/corrections/dashen").mkdir(parents=True)
    (tmp_path / "derived/ocr/corrections/dashen/aaaa.json").write_text(
        json.dumps(
            {
                "id": "dashen.aaaa",
                "corrections": [
                    {"image_index": 1, "block_index": 5, "original": "已经变了", "corrected": "改过的行"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(edition.EditionError, match="aaaa"):
        edition.build(tmp_path)


def test_check_reports_missing_stale_and_orphan(tmp_path):
    _seed(tmp_path)
    edition.write(tmp_path)
    assert edition.check(tmp_path) == []

    (tmp_path / "edition/dashen/aaaa.md").write_text("tampered\n", encoding="utf-8")
    (tmp_path / "edition/notices.jsonl").unlink()
    (tmp_path / "edition/dashen/zzzz.md").write_text("old\n", encoding="utf-8")
    problems = edition.check(tmp_path)

    assert "stale edition/dashen/aaaa.md" in problems
    assert "missing edition/notices.jsonl" in problems
    assert "orphan edition/dashen/zzzz.md" in problems


def test_write_is_idempotent(tmp_path):
    _seed(tmp_path)

    assert edition.write(tmp_path)
    assert edition.write(tmp_path) == []


def test_committed_edition_is_current():
    assert edition.check() == []


def test_committed_edition_covers_every_update_notice():
    outputs = edition.build()
    markdown = [path for path in outputs if path.suffix == ".md"]

    assert len(markdown) == 162
    assert len(outputs[edition.INDEX_PATH].splitlines()) == 162


def test_index_is_ordered_by_publication():
    rows = [json.loads(line) for line in edition.build()[edition.INDEX_PATH].splitlines()]
    stamps = [row["published_at"] for row in rows]

    assert stamps == sorted(stamps)


def test_committed_text_keeps_the_repaired_glyphs():
    texts = [
        path.read_text(encoding="utf-8")
        for path in sorted((archive.REPO_ROOT / edition.EDITION_DIR).glob("*.md"))
    ]

    assert not any("骆" in text for text in texts)
    assert not any("噢幻草" in text for text in texts)
    assert any("驺吾" in text for text in texts)
    assert any("嗅幻草" in text for text in texts)
