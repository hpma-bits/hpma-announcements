import json
from pathlib import Path

from collectors import dashen

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
