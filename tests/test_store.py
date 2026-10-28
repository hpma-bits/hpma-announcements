import json

from collectors.model import Image, Notice
from collectors.store import Store


def make_notice(**overrides) -> Notice:
    values = {
        "id": "dashen.6adf31ceee0069337849bde8",
        "url": "https://ds.163.com/feed/6adf31ceee0069337849bde8/",
        "published_at": "2026-10-26T18:56:00+08:00",
        "source_updated_at": "2026-10-26T18:56:14+08:00",
        "notice_date": "2026-10-28",
        "title": "【10月28日更新公告】",
        "text": "服务器将于10月28日 6:00-9:00 停服维护。",
        "images": [
            Image(
                index=1,
                url="https://ok.166.net/reunionpub/example.jpg",
                path="assets/dashen/6adf31ceee0069337849bde8/01.jpg",
                sha256="a" * 64,
                bytes=296245,
                width=640,
                height=1234,
            )
        ],
    }
    values.update(overrides)
    return Notice(**values)


def test_write_creates_record(tmp_path):
    store = Store(tmp_path)
    result = store.write(make_notice())

    assert result.action == "created"
    assert result.revision == 1
    assert result.path == "data/dashen/2026/6adf31ceee0069337849bde8.json"

    data = json.loads((tmp_path / result.path).read_text(encoding="utf-8"))
    assert data["id"] == "dashen.6adf31ceee0069337849bde8"
    assert data["first_seen_at"].endswith("Z")
    assert len(data["content_hash"]) == 64
    assert data["images"][0]["width"] == 640


def test_write_is_idempotent(tmp_path):
    store = Store(tmp_path)
    first = store.write(make_notice())
    before = (tmp_path / first.path).read_bytes()

    second = store.write(make_notice())
    after = (tmp_path / second.path).read_bytes()

    assert second.action == "unchanged"
    assert second.revision == first.revision
    assert before == after


def test_write_updates_revision_and_keeps_first_seen(tmp_path):
    store = Store(tmp_path)
    first = store.write(make_notice())
    first_seen = json.loads((tmp_path / first.path).read_text(encoding="utf-8"))["first_seen_at"]

    second = store.write(make_notice(text="更新后的公告正文"))
    data = json.loads((tmp_path / second.path).read_text(encoding="utf-8"))

    assert second.action == "updated"
    assert second.revision == 2
    assert data["first_seen_at"] == first_seen
    assert data["revision"] == 2
    assert data["text"] == "更新后的公告正文"


def test_index_is_sorted_and_deterministic(tmp_path):
    store = Store(tmp_path)
    store.write(
        make_notice(
            id="dashen.a",
            url="https://ds.163.com/feed/a/",
            published_at="2026-10-01T00:00:00+08:00",
            notice_date="2026-10-01",
        )
    )
    store.write(
        make_notice(
            id="dashen.b",
            url="https://ds.163.com/feed/b/",
            published_at="2026-10-20T00:00:00+08:00",
            notice_date="2026-10-20",
        )
    )

    path = store.write_index()
    text = path.read_text(encoding="utf-8")
    index = json.loads(text)

    assert index["count"] == 2
    assert [item["id"] for item in index["items"]] == ["dashen.b", "dashen.a"]

    store.write_index()
    assert path.read_text(encoding="utf-8") == text


def test_validate_empty_root_passes(tmp_path):
    assert Store(tmp_path).validate() == []


def test_validate_reports_schema_errors(tmp_path):
    store = Store(tmp_path)
    path = tmp_path / "data" / "dashen" / "2026" / "bad.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"id": "dashen.bad"}\n', encoding="utf-8")

    errors = store.validate()

    assert errors
    assert any("bad.json" in error for error in errors)


def test_validate_reports_duplicate_ids(tmp_path):
    store = Store(tmp_path)
    result = store.write(make_notice())
    original = json.loads((tmp_path / result.path).read_text(encoding="utf-8"))

    duplicate = tmp_path / "data" / "dashen" / "2025" / "duplicate.json"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    errors = store.validate()

    assert any("duplicate id" in error for error in errors)
