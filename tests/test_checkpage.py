import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_checkpage", REPO / "tools" / "build_checkpage.py")
checkpage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checkpage)

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + (13).to_bytes(4, "big")
    + b"IHDR"
    + (640).to_bytes(4, "big")
    + (1234).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
    + b"\x00" * 8
)


def _seed(root: Path) -> None:
    (root / "corpus" / "dashen").mkdir(parents=True)
    (root / "study").mkdir()
    (root / "derived" / "ocr" / "dashen").mkdir(parents=True)
    asset_dir = root / "assets" / "dashen" / "aaaa"
    asset_dir.mkdir(parents=True)
    (asset_dir / "01.png").write_bytes(PNG)

    post = {
        "id": "aaaa",
        "type": 2,
        "published_at": "2026-01-01T00:00:00+08:00",
        "source_updated_at": "2026-01-01T00:00:05+08:00",
        "title": "【1月1日更新公告】",
        "text": "维护窗口",
        "long_text": "",
        "image_count": 1,
        "image_urls": ["https://ok.166.net/a"],
        "topics": ["哈利波特新情报"],
        "deleted": False,
        "feed_url": "https://ds.163.com/feed/aaaa/",
    }
    (root / "corpus" / "dashen" / "posts.jsonl").write_text(
        json.dumps(post, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "study" / "labels.jsonl").write_text(
        json.dumps(
            {
                "id": "aaaa",
                "label": "update",
                "confidence": "high",
                "evidence": "标题命中更新公告",
                "notice_date": "2026-01-01",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    blocks = [
        {"box": [[10, 20], [30, 20], [30, 40], [10, 40]], "text": "第一行", "score": 0.95},
        {"box": [[10, 50], [90, 50], [90, 70], [10, 70]], "text": "第二行", "score": 0.75},
    ]
    record = {
        "id": "dashen.aaaa",
        "family": "update",
        "engine": {"name": "fake", "version": "0"},
        "source": {
            "title": "【1月1日更新公告】",
            "published_at": "2026-01-01T00:00:00+08:00",
            "url": "https://ds.163.com/feed/aaaa/",
        },
        "text": "第一行\n第二行",
        "images": [
            {
                "index": 1,
                "path": "assets/dashen/aaaa/01.png",
                "url": "https://ok.166.net/a",
                "bytes": len(PNG),
                "sha256": "0" * 64,
                "text": "第一行\n第二行",
                "blocks": blocks,
            }
        ],
    }
    (root / "derived" / "ocr" / "dashen" / "aaaa.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )

    broken = {
        "id": "dashen.bbbb",
        "family": "update",
        "engine": {"name": "fake", "version": "0"},
        "source": {"title": "【2月2日更新公告】", "published_at": "2026-02-02T00:00:00+08:00"},
        "text": "",
        "images": [
            {
                "index": 1,
                "path": "assets/dashen/bbbb/01.jpg",
                "url": "https://ok.166.net/b",
                "bytes": 0,
                "sha256": "",
                "text": "",
                "blocks": [],
            }
        ],
    }
    (root / "derived" / "ocr" / "dashen" / "bbbb.json").write_text(
        json.dumps(broken, ensure_ascii=False), encoding="utf-8"
    )


def _data(out_dir: Path):
    text = (out_dir / "data.js").read_text(encoding="utf-8")
    return json.loads(text[text.index("=") + 1 :].rstrip().rstrip(";"))


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_image_size_parsers(tmp_path):
    gif = b"GIF89a" + (640).to_bytes(2, "little") + (1234).to_bytes(2, "little") + b"\x00" * 8
    assert checkpage.image_size(_write(tmp_path, "a.gif", gif)) == (640, 1234)

    jpeg = (
        b"\xff\xd8\xff\xe0"
        + (16).to_bytes(2, "big")
        + b"JFIF\x00"
        + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + (1234).to_bytes(2, "big")
        + (640).to_bytes(2, "big")
        + b"\x03"
        + b"\x00" * 9
    )
    assert checkpage.image_size(_write(tmp_path, "a.jpg", jpeg)) == (640, 1234)

    vp8x = (
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBP"
        + b"VP8X"
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (640 - 1).to_bytes(3, "little")
        + (1234 - 1).to_bytes(3, "little")
        + b"\x00"
    )
    assert checkpage.image_size(_write(tmp_path, "a.webp", vp8x)) == (640, 1234)

    packed = (640 - 1) | ((1234 - 1) << 14)
    vp8l = (
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBP"
        + b"VP8L"
        + (5).to_bytes(4, "little")
        + b"\x2f"
        + packed.to_bytes(4, "little")
    )
    assert checkpage.image_size(_write(tmp_path, "b.webp", vp8l)) == (640, 1234)

    vp8 = (
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBP"
        + b"VP8 "
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00"
        + b"\x9d\x01\x2a"
        + (640).to_bytes(2, "little")
        + (1234).to_bytes(2, "little")
    )
    assert checkpage.image_size(_write(tmp_path, "c.webp", vp8)) == (640, 1234)

    assert checkpage.image_size(_write(tmp_path, "d.bin", b"not an image")) is None


def test_checkpage_build_and_check(tmp_path):
    _seed(tmp_path)
    out_dir = tmp_path / "checkpage"

    payload, warnings, changed = checkpage.build(tmp_path, out_dir)
    assert changed == {"index.html": True, "data.js": True}
    assert any("missing image" in warning for warning in warnings)
    assert payload["summary"] == {
        "notices": 2,
        "images": 2,
        "blocks": 2,
        "chars": 7,
        "lowBlocks": 1,
        "veryLowBlocks": 1,
        "missingImages": 1,
        "noticesWithIssues": 1,
    }

    data = _data(out_dir)
    assert data["thresholds"] == {"low": 0.9, "veryLow": 0.8}
    assert data["engines"] == [{"name": "fake", "version": "0"}]
    assert [notice["id"] for notice in data["notices"]] == ["dashen.aaaa", "dashen.bbbb"]

    notice = data["notices"][0]
    assert notice["label"] == "update"
    assert notice["noticeDate"] == "2026-01-01"
    assert notice["bodyText"] == "维护窗口"
    assert notice["stats"] == {
        "images": 1,
        "blocks": 2,
        "chars": 7,
        "mean": 0.85,
        "low": 1,
        "veryLow": 1,
        "emptyImages": 0,
    }
    image = notice["images"][0]
    assert image["src"] == "../assets/dashen/aaaa/01.png"
    assert (image["width"], image["height"]) == (640, 1234)
    assert image["stats"] == {"blocks": 2, "chars": 6, "mean": 0.85, "low": 1, "veryLow": 1}
    assert image["blocks"][0] == {"x": 10, "y": 20, "w": 20, "h": 20, "text": "第一行", "score": 0.95}

    broken = data["notices"][1]
    assert broken["images"][0]["missing"] is True
    assert "缺图 1" in broken["issues"]
    assert "OCR 文本为空" in broken["issues"]

    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "OCR 核对台" in html
    assert "/*{{STYLE}}*/" not in html and "/*{{APP}}*/" not in html
    assert "hpma-checkpage-review-v1" in html
    assert str(tmp_path) not in (out_dir / "data.js").read_text(encoding="utf-8")

    # Rerunning is a zero diff.
    _, _, changed_again = checkpage.build(tmp_path, out_dir)
    assert changed_again == {"index.html": False, "data.js": False}
    assert checkpage.main(["--root", str(tmp_path), "--check", "--quiet"]) == 0

    # A hand-edit is detected by --check.
    with (out_dir / "data.js").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert checkpage.main(["--root", str(tmp_path), "--check", "--quiet"]) == 1
