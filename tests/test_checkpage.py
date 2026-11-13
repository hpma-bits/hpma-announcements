import json

from tools import checkpage


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed(root):
    (root / "derived/ocr/dashen").mkdir(parents=True)
    _write_jsonl(
        root / "corpus/dashen/posts.jsonl",
        [
            {
                "id": "aaaa",
                "title": "【1月2日更新公告】",
                "published_at": "2026-01-01T22:00:00+08:00",
                "text": "正文第一行\n正文第二行",
                "feed_url": "https://ds.163.com/feed/aaaa/",
            }
        ],
    )
    _write_jsonl(
        root / "study/labels.jsonl",
        [{"id": "aaaa", "label": "update", "notice_date": "2026-01-02"}],
    )
    (root / "derived/ocr/dashen/aaaa.json").write_text(
        json.dumps(
            {
                "id": "dashen.aaaa",
                "images": [
                    {
                        "index": 1,
                        "path": "assets/dashen/aaaa/01.png",
                        "blocks": [
                            {
                                "box": [[10, 10], [90, 10], [90, 40], [10, 40]],
                                "text": "图片第一行",
                            },
                            {
                                "box": [[10, 50], [90, 50], [90, 80], [10, 80]],
                                "text": "图片第二行",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
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
                        "block_index": 1,
                        "original": "图片第二行",
                        "corrected": "图片第二行（修正）",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        root / "corpus/nga/posts.jsonl",
        [
            {
                "id": "123",
                "title": "NGA 公告",
                "published_at": "2026-01-01T23:00:00+08:00",
                "text": "NGA 正文\n第二行",
                "thread_url": "https://bbs.nga.cn/read.php?tid=123",
            }
        ],
    )
    _write_jsonl(
        root / "study/labels-nga.jsonl",
        [{"id": "123", "label": "update", "notice_date": "2026-01-02"}],
    )


def _rows(html):
    payload = html.split("const NOTICES = ", 1)[1].split(";\n", 1)[0]
    return json.loads(payload)


def test_build_includes_body_and_image_text(tmp_path):
    _seed(tmp_path)

    rows = _rows(checkpage.build(tmp_path))
    dashen = next(row for row in rows if row["id"] == "dashen.aaaa")
    nga = next(row for row in rows if row["id"] == "nga.123")

    assert dashen["body"] == "正文第一行\n正文第二行"
    assert dashen["image_text"] == "图片第一行\n图片第二行（修正）"
    assert dashen["canonical"] is False
    assert nga["body"] == "NGA 正文\n第二行"
    assert nga["image_text"] == ""
    assert nga["canonical"] is True


def test_build_is_deterministic_and_write_checks(tmp_path):
    _seed(tmp_path)

    assert checkpage.build(tmp_path) == checkpage.build(tmp_path)
    path = checkpage.write(tmp_path)
    assert path == tmp_path / "checkpage/index.html"
    assert checkpage.check(tmp_path) == []

    path.write_text("stale\n", encoding="utf-8")
    assert checkpage.check(tmp_path) == [f"stale {path.as_posix()}"]

    path.unlink()
    assert checkpage.check(tmp_path) == [f"missing {path.as_posix()}"]
