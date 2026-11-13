import json
from pathlib import Path

import pytest

from collectors import cli, dashen

FIXTURE = Path(__file__).parent / "fixtures" / "dashen_feed_page1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_page_extracts_titles_and_media():
    records, next_max_time = dashen.parse_page(_fixture())
    by_id = {record["id"]: record for record in records}

    notice = by_id["6adf31ceee0069337849bde8"]
    assert notice["type"] == 2
    assert notice["title"] == "【10月28日更新公告】"
    assert notice["published_at"].startswith("2026-10-26T18:56:")
    assert notice["image_count"] == 4
    assert notice["feed_url"] == "https://ds.163.com/feed/6adf31ceee0069337849bde8/"
    assert notice["long_text"] is None

    article = by_id["6ae03de8446a4f1a7072d971"]
    assert article["type"] == 6
    assert article["title"] == "✦11月学院礼盒前瞻✦"
    assert article["long_text"].startswith("<p>")
    assert article["feed_url"] == "https://ds.163.com/article/6ae03de8446a4f1a7072d971/"

    assert next_max_time == _fixture()["result"]["nextRangeParam"]["maxTime"]


def test_extract_title_fallbacks():
    assert dashen.extract_title({"title": "  T "}) == "T"
    assert dashen.extract_title({"subTitle": "S", "text": "x"}) == "S"
    assert dashen.extract_title({"text": "first\nsecond"}) == "first"
    assert dashen.extract_title({}) is None


def _post(post_id: str, minutes: int) -> dict:
    stamp = 1_790_000_000_000 + minutes * 60_000
    return {
        "id": post_id,
        "type": 2,
        "createTime": stamp,
        "updateTime": stamp,
        "deleteTime": 0,
        "topicInfoList": [],
        "content": json.dumps(
            {"type": 2, "body": {"text": f"{post_id} 正文\n第二行"}}, ensure_ascii=False
        ),
    }


def _pages() -> list[dict]:
    cursor = 1_790_000_600_000
    return [
        {"result": {"feeds": [_post("bbbb", 10), _post("aaaa", 20)], "nextRangeParam": {"maxTime": cursor}}},
        {"result": {"feeds": [_post("cccc", 0), _post("bbbb", 10)], "nextRangeParam": {"maxTime": cursor}}},
    ]


def test_harvest_corpus_dedupes_sorts_and_is_idempotent(tmp_path):
    pages = _pages()
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return pages[(len(calls) - 1) % len(pages)]

    first = dashen.harvest_corpus(tmp_path, fetch=fetch)
    assert first["post_count"] == 3
    assert first["changed"] is True

    posts_path = tmp_path / "corpus" / "dashen" / "posts.jsonl"
    lines = posts_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["id"] for line in lines] == ["cccc", "bbbb", "aaaa"]

    manifest_path = tmp_path / "corpus" / "dashen" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["post_count"] == 3
    assert manifest["page_count"] == 2

    manifest_before = manifest_path.read_text(encoding="utf-8")
    second = dashen.harvest_corpus(tmp_path, fetch=fetch)
    assert second["changed"] is False
    assert second["post_count"] == 3
    assert manifest_path.read_text(encoding="utf-8") == manifest_before


def test_harvest_respects_max_pages(tmp_path):
    pages = _pages()
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return pages[(len(calls) - 1) % len(pages)]

    summary = dashen.harvest_corpus(tmp_path, max_pages=1, fetch=fetch)

    assert summary["page_count"] == 1
    assert summary["post_count"] == 2
    assert len(calls) == 1


def _harvest(tmp_path, pages: list[dict], **kwargs):
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return pages[(len(calls) - 1) % len(pages)]

    summary = dashen.harvest_corpus(tmp_path, fetch=fetch, **kwargs)
    return summary, calls


def _posts_path(tmp_path) -> Path:
    return tmp_path / "corpus" / "dashen" / "posts.jsonl"


def test_max_pages_keeps_rows_from_a_complete_corpus(tmp_path):
    pages = _pages()
    full, _ = _harvest(tmp_path, pages)
    assert full["post_count"] == 3
    before = _posts_path(tmp_path).read_bytes()

    partial, calls = _harvest(tmp_path, pages, max_pages=1)

    assert len(calls) == 1
    assert partial["post_count"] == 3
    assert partial["dropped"] == []
    assert _posts_path(tmp_path).read_bytes() == before
    assert partial["changed"] is False


def test_allow_shrink_explicitly_replaces_the_corpus(tmp_path):
    pages = _pages()
    _harvest(tmp_path, pages)

    partial, _ = _harvest(tmp_path, pages, max_pages=1, allow_shrink=True)

    assert partial["post_count"] == 2
    assert partial["dropped"] == ["cccc"]
    ids = [
        json.loads(line)["id"]
        for line in _posts_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    assert ids == ["bbbb", "aaaa"]


def test_out_writes_a_separate_smoke_corpus(tmp_path):
    smoke = tmp_path / "smoke"
    summary, _ = _harvest(tmp_path, _pages(), max_pages=1, out=smoke)

    assert summary["post_count"] == 2
    assert (smoke / "posts.jsonl").exists()
    assert (smoke / "manifest.json").exists()
    assert not _posts_path(tmp_path).exists()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"result": {}},
        {"result": {"feeds": []}},
        {"result": {"feeds": "not-a-list"}},
        {"result": {"feeds": [{"type": 2}]}},
    ],
)
def test_malformed_response_never_overwrites_the_corpus(tmp_path, payload):
    _harvest(tmp_path, _pages())
    before = _posts_path(tmp_path).read_bytes()

    with pytest.raises(dashen.DashenError):
        dashen.harvest_corpus(tmp_path, fetch=lambda url: payload)

    assert _posts_path(tmp_path).read_bytes() == before


def test_empty_response_does_not_create_a_corpus(tmp_path):
    with pytest.raises(dashen.DashenError, match="no posts"):
        dashen.harvest_corpus(
            tmp_path, fetch=lambda url: {"result": {"feeds": []}}
        )

    assert not _posts_path(tmp_path).exists()


def test_atomic_write_failure_keeps_the_previous_corpus(tmp_path, monkeypatch):
    _harvest(tmp_path, _pages())
    before = _posts_path(tmp_path).read_bytes()

    def fetch(url: str) -> dict:
        return {"result": {"feeds": [_post("dddd", 30)], "nextRangeParam": {}}}

    def boom(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        dashen.harvest_corpus(tmp_path, fetch=fetch)

    assert _posts_path(tmp_path).read_bytes() == before
    leftovers = list((tmp_path / "corpus" / "dashen").glob("*.tmp"))
    assert leftovers == []


def test_cli_passes_safety_flags_and_reports_errors(monkeypatch, capsys):
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return {"post_count": 0, "page_count": 0, "changed": False}

    monkeypatch.setattr(cli.dashen, "harvest_corpus", fake)
    assert (
        cli.main(
            ["corpus", "--max-pages", "1", "--out", "smoke", "--allow-shrink"]
        )
        == 0
    )
    assert captured["max_pages"] == 1
    assert captured["out"] == "smoke"
    assert captured["allow_shrink"] is True

    def fail(**kwargs):
        raise dashen.DashenError("empty feeds")

    monkeypatch.setattr(cli.dashen, "harvest_corpus", fail)
    assert cli.main(["corpus"]) == 1
    assert "empty feeds" in capsys.readouterr().err
