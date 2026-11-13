import json
from pathlib import Path

import pytest

from collectors import cli, nga

INDEX_PAGE = """<html><body>
<p id='postcontent0' class='postcontent ubbcode'>
===2021.9===
[url=/read.php?tid=100]9.15 正式服维护详情[/url]
[url=/read.php?tid=101]9.15 体验服维护详情[/url]
===2022.2===
[url=/read.php?tid=102]2.1 正式服维护详情[/url]
</p>
</body></html>
"""

NOTICE = (
    "亲爱的巫师：\n\n"
    "为了给大家带来更好的游戏体验，服务器将于9月15日(周三)早上2:00-7:00进行停服维护。\n\n"
    "===重要更新===\n\n"
    + "活动内容。" * 80
    + "\n\n===体验优化===\n\n"
    + "优化内容。" * 80
)


def _thread_page(
    text: str = NOTICE,
    *,
    subject: str = "正式服9月15日更新公告",
    posted: str = "2021-09-14 13:39",
    uid: str = "12345",
) -> str:
    return f"""<html><body>
<h3 id='postsubject0'>{subject}</h3>
<span id='postdate0' title='reply time'>{posted}</span>
<span id='posterinfo0' class='posterinfo'><a href='nuke.php?func=ucp&uid={uid}'>x</a></span></td>
<p id='postcontent0' class='postcontent ubbcode'>[quote]{text}[/quote]</p>
</body></html>"""


def test_parse_index_reads_months_dates_and_kinds():
    entries = nga.parse_index(INDEX_PAGE)

    assert [entry["tid"] for entry in entries] == ["100", "101", "102"]
    assert entries[0]["kind"] == "production"
    assert entries[1]["kind"] == "test"
    assert entries[0]["notice_date"] == "2021-09-15"
    assert entries[2]["notice_date"] == "2022-02-01"
    assert entries[0]["month"] == "2021.9"
    assert entries[0]["thread_url"] == "https://bbs.nga.cn/read.php?tid=100"


def test_bbcode_to_text_keeps_the_notice_brackets():
    text = nga.bbcode_to_text(
        "[color=red]活动时间[/color]\n"
        "[赛季新礼盒]\n"
        "[url=/read.php?tid=1]链接[/url]\n"
        "[img]./x.jpg[/img]\n"
        "[s:ac:茶]"
    )

    assert "活动时间" in text
    assert "[赛季新礼盒]" in text
    assert "链接" in text
    assert "x.jpg" not in text
    assert "[color" not in text


def test_notice_text_skips_the_collapsed_image_version():
    text = nga.notice_text(
        "[quote][collapse=图片版公告][img]./a.jpg[/img][/collapse][/quote]"
        f"[quote]{NOTICE}[/quote]"
    )

    assert text.startswith("亲爱的巫师")
    assert "===重要更新===" in text
    assert "a.jpg" not in text
    assert "[quote]" not in text


def test_parse_thread_reads_first_post_and_provenance():
    record = nga.parse_thread(
        _thread_page(), tid="100", notice_date="2021-09-15", page_sha256="abc"
    )

    assert record["id"] == "100"
    assert record["source"] == "nga"
    assert record["title"] == "正式服9月15日更新公告"
    assert record["published_at"] == "2021-09-14T13:39:00+08:00"
    assert record["author_uid"] == "12345"
    assert record["notice_date"] == "2021-09-15"
    assert record["text"].startswith("亲爱的巫师")
    assert record["page_sha256"] == "abc"
    assert len(record["raw_sha256"]) == 64


def test_parse_thread_refuses_a_mismatched_date():
    page = _thread_page(NOTICE.replace("9月15日", "9月16日", 1))

    with pytest.raises(nga.NgaError, match="does not match"):
        nga.parse_thread(page, tid="100", notice_date="2021-09-15")


def test_parse_thread_refuses_a_post_without_an_update_section():
    page = _thread_page("亲爱的巫师：9月15日停服维护。\n\n" + "正文。" * 100)

    with pytest.raises(nga.NgaError, match="no update section"):
        nga.parse_thread(page, tid="100", notice_date="2021-09-15")


def test_harvest_selects_only_production_before_the_boundary(tmp_path):
    pages = {
        "https://bbs.nga.cn/read.php?tid=27915340": INDEX_PAGE.encode("gb18030"),
        "https://bbs.nga.cn/read.php?tid=100": _thread_page().encode("gb18030"),
    }
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return pages[url]

    summary = nga.harvest(tmp_path, before="2022-01-26", fetch=fetch, delay=0)

    assert summary["post_count"] == 1
    assert summary["entry_count"] == 3
    assert len(calls) == 2

    posts = [
        json.loads(line)
        for line in (tmp_path / "corpus/nga/posts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [post["id"] for post in posts] == ["100"]
    assert (tmp_path / "corpus/nga/raw/100.html").exists()
    index = json.loads((tmp_path / "corpus/nga/index.json").read_text(encoding="utf-8"))
    assert index["production_count"] == 2
    assert index["test_count"] == 1


def test_harvest_is_idempotent(tmp_path):
    pages = {
        "https://bbs.nga.cn/read.php?tid=27915340": INDEX_PAGE.encode("gb18030"),
        "https://bbs.nga.cn/read.php?tid=100": _thread_page().encode("gb18030"),
    }

    def fetch(url: str) -> bytes:
        return pages[url]

    first = nga.harvest(tmp_path, before="2022-01-26", fetch=fetch, delay=0)
    manifest = tmp_path / "corpus/nga/manifest.json"
    before = manifest.read_text(encoding="utf-8")
    second = nga.harvest(tmp_path, before="2022-01-26", fetch=fetch, delay=0)

    assert first["changed"] is True
    assert second["changed"] is False
    assert manifest.read_text(encoding="utf-8") == before


def test_harvest_keeps_the_first_page_hash_when_only_chrome_changes(tmp_path):
    index = INDEX_PAGE.encode("gb18030")
    thread = _thread_page().encode("gb18030")
    pages = {
        "https://bbs.nga.cn/read.php?tid=27915340": index,
        "https://bbs.nga.cn/read.php?tid=100": thread,
    }

    def fetch(url: str) -> bytes:
        return pages[url]

    nga.harvest(tmp_path, before="2022-01-26", fetch=fetch, delay=0)
    posts_path = tmp_path / "corpus/nga/posts.jsonl"
    first = json.loads(posts_path.read_text(encoding="utf-8").splitlines()[0])

    changed_thread = _thread_page().replace("</body>", "<p>page chrome</p></body>")
    pages["https://bbs.nga.cn/read.php?tid=100"] = changed_thread.encode("gb18030")
    second = nga.harvest(tmp_path, before="2022-01-26", fetch=fetch, delay=0)
    after = json.loads(posts_path.read_text(encoding="utf-8").splitlines()[0])

    assert second["changed"] is False
    assert after["page_sha256"] == first["page_sha256"]
    assert after["raw_sha256"] == first["raw_sha256"]


def test_notice_text_prefers_the_production_collapse():
    production = "正式服正文。" * 40 + "\n\n===重要更新===\n\n" + "活动。" * 40
    test_server = "体验服正文。" * 40
    text = nga.notice_text(
        f"[collapse=体验服]{test_server}[/collapse]"
        f"[collapse=正式服]{production}[/collapse]"
    )

    assert "正式服正文" in text
    assert "体验服正文" not in text


def test_notice_text_drops_deleted_text():
    text = nga.notice_text(
        "旧内容[del]已经删掉的内容[/del]新内容\n\n" + "正文。" * 100
    )

    assert "已经删掉的内容" not in text
    assert "新内容" in text


def test_harvest_without_a_boundary_takes_every_production_notice(tmp_path):
    pages = {
        "https://bbs.nga.cn/read.php?tid=27915340": INDEX_PAGE.encode("gb18030"),
        "https://bbs.nga.cn/read.php?tid=100": _thread_page().encode("gb18030"),
        "https://bbs.nga.cn/read.php?tid=102": _thread_page(
            NOTICE.replace("9月15日", "2月1日"),
            subject="正式服2月1日更新公告",
            posted="2022-01-31 18:00",
        ).encode("gb18030"),
    }

    def fetch(url: str) -> bytes:
        return pages[url]

    summary = nga.harvest(tmp_path, fetch=fetch, delay=0)

    assert summary["post_count"] == 2
    assert summary["boundary"] == "all"


def test_harvest_can_read_pages_saved_on_disk(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "27915340.html").write_bytes(INDEX_PAGE.encode("gb18030"))
    (pages / "100.html").write_bytes(_thread_page().encode("gb18030"))

    summary = nga.harvest(
        tmp_path, before="2022-01-26", from_dir=pages, delay=0
    )

    assert summary["post_count"] == 1
    assert (tmp_path / "corpus/nga/posts.jsonl").exists()


def test_committed_nga_corpus_and_selection_agree():
    root = Path(__file__).resolve().parents[1]
    posts = {
        post["id"]: post
        for post in (
            json.loads(line)
            for line in (root / "corpus/nga/posts.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    labels = [
        json.loads(line)
        for line in (root / "study/labels-nga.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(posts) == 58
    assert len(labels) == 58
    assert min(label["notice_date"] for label in labels) == "2021-09-15"
    assert max(label["notice_date"] for label in labels) == "2022-11-16"
    for label in labels:
        post = posts[label["id"]]
        assert post["notice_date"] == label["notice_date"]
        raw = (root / post["raw_path"]).read_text(encoding="utf-8")
        assert nga.sha256(raw.encode("utf-8")) == post["raw_sha256"]


def test_committed_nga_splits_into_gap_and_overlap():
    root = Path(__file__).resolve().parents[1]
    nga_dates = {
        json.loads(line)["notice_date"]
        for line in (root / "study/labels-nga.jsonl").read_text(encoding="utf-8").splitlines()
    }
    dashen_dates = {
        row["notice_date"]
        for row in (
            json.loads(line)
            for line in (root / "study/labels.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if row["label"] == "update"
    }

    gap = nga_dates - dashen_dates
    overlap = nga_dates & dashen_dates
    assert len(gap) == 18
    assert len(overlap) == 40
    assert max(gap) < "2022-01-26"
    assert min(overlap) >= "2022-01-26"


def test_committed_index_keeps_the_full_nga_coverage_map():
    root = Path(__file__).resolve().parents[1]
    index = json.loads((root / "corpus/nga/index.json").read_text(encoding="utf-8"))

    assert index["entry_count"] == 116
    assert index["production_count"] == 58
    assert index["test_count"] == 58
    assert index["entries"][0]["month"] == "2022.11"


def test_server_section_with_only_a_test_block_returns_the_whole_post():
    value = "前言\n[collapse=体验服]体验服正文[/collapse]\n后记"

    assert nga.server_section(value) == value


def test_notice_text_with_only_a_test_block_uses_the_whole_post():
    value = (
        "公告前言。" * 40
        + "\n[collapse=体验服]"
        + "体验服正文。" * 40
        + "[/collapse]\n===重要更新===\n"
        + "活动内容。" * 40
    )

    text = nga.notice_text(value)

    assert "公告前言" in text
    assert "活动内容" in text


def _full_pages() -> dict[str, bytes]:
    return {
        "https://bbs.nga.cn/read.php?tid=27915340": INDEX_PAGE.encode("gb18030"),
        "https://bbs.nga.cn/read.php?tid=100": _thread_page().encode("gb18030"),
        "https://bbs.nga.cn/read.php?tid=102": _thread_page(
            NOTICE.replace("9月15日", "2月1日", 1),
            subject="正式服2月1日更新公告",
            posted="2022-01-31 18:00",
        ).encode("gb18030"),
    }


def _nga_posts(tmp_path) -> Path:
    return tmp_path / "corpus" / "nga" / "posts.jsonl"


def _seed_full_corpus(tmp_path) -> dict[str, bytes]:
    pages = _full_pages()

    def fetch(url: str) -> bytes:
        return pages[url]

    summary = nga.harvest(tmp_path, fetch=fetch, delay=0)
    assert summary["post_count"] == 2
    return pages


def test_before_boundary_keeps_rows_from_a_complete_corpus(tmp_path):
    pages = _seed_full_corpus(tmp_path)
    before = _nga_posts(tmp_path).read_bytes()

    def fetch(url: str) -> bytes:
        return pages[url]

    partial = nga.harvest(tmp_path, before="2022-01-26", fetch=fetch, delay=0)

    assert partial["selected"] == 1
    assert partial["post_count"] == 2
    assert partial["dropped"] == []
    assert _nga_posts(tmp_path).read_bytes() == before
    assert partial["changed"] is False


def test_limit_keeps_rows_from_a_complete_corpus(tmp_path):
    pages = _seed_full_corpus(tmp_path)
    before = _nga_posts(tmp_path).read_bytes()

    def fetch(url: str) -> bytes:
        return pages[url]

    partial = nga.harvest(tmp_path, limit=1, fetch=fetch, delay=0)

    assert partial["selected"] == 1
    assert partial["post_count"] == 2
    assert _nga_posts(tmp_path).read_bytes() == before


def test_out_writes_a_separate_nga_corpus(tmp_path):
    pages = _full_pages()
    smoke = tmp_path / "smoke"

    summary = nga.harvest(
        tmp_path, out=smoke, fetch=lambda url: pages[url], delay=0
    )

    assert summary["post_count"] == 2
    assert (smoke / "posts.jsonl").exists()
    assert (smoke / "index.json").exists()
    assert not (tmp_path / "corpus").exists()


def test_allow_shrink_explicitly_replaces_the_corpus(tmp_path):
    pages = _seed_full_corpus(tmp_path)

    def fetch(url: str) -> bytes:
        return pages[url]

    partial = nga.harvest(
        tmp_path, before="2022-01-26", fetch=fetch, delay=0, allow_shrink=True
    )

    assert partial["post_count"] == 1
    assert partial["dropped"] == ["102"]
    ids = [
        json.loads(line)["id"]
        for line in _nga_posts(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    assert ids == ["100"]


def test_from_dir_partial_run_keeps_previous_rows(tmp_path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    pages = _full_pages()
    for tid in ("27915340", "100", "102"):
        (pages_dir / f"{tid}.html").write_bytes(
            pages[f"https://bbs.nga.cn/read.php?tid={tid}"]
        )

    full = nga.harvest(tmp_path, from_dir=pages_dir, delay=0)
    assert full["post_count"] == 2
    before = _nga_posts(tmp_path).read_bytes()

    partial = nga.harvest(tmp_path, before="2022-01-26", from_dir=pages_dir, delay=0)

    assert partial["post_count"] == 2
    assert _nga_posts(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "index_bytes",
    [
        b"",
        "<html><body>not NGA</body></html>".encode("gb18030"),
        "<p id='postcontent0'>没有维护链接</p>".encode("gb18030"),
    ],
)
def test_empty_or_malformed_index_never_overwrites_the_corpus(tmp_path, index_bytes):
    _seed_full_corpus(tmp_path)
    before = _nga_posts(tmp_path).read_bytes()

    def fetch(url: str) -> bytes:
        return index_bytes

    with pytest.raises(nga.NgaError):
        nga.harvest(tmp_path, fetch=fetch, delay=0)

    assert _nga_posts(tmp_path).read_bytes() == before


def test_malformed_thread_page_never_overwrites_the_corpus(tmp_path):
    pages = _seed_full_corpus(tmp_path)
    before = _nga_posts(tmp_path).read_bytes()

    def fetch(url: str) -> bytes:
        if url.endswith("27915340"):
            return pages[url]
        return b"<html><body>no first post</body></html>"

    with pytest.raises(nga.NgaError, match="tid 100"):
        nga.harvest(tmp_path, fetch=fetch, delay=0)

    assert _nga_posts(tmp_path).read_bytes() == before


def test_empty_index_does_not_create_a_corpus(tmp_path):
    with pytest.raises(nga.NgaError):
        nga.harvest(tmp_path, fetch=lambda url: b"", delay=0)

    assert not _nga_posts(tmp_path).exists()


def test_atomic_write_failure_keeps_the_previous_corpus(tmp_path, monkeypatch):
    pages = _seed_full_corpus(tmp_path)
    before = _nga_posts(tmp_path).read_bytes()
    pages["https://bbs.nga.cn/read.php?tid=100"] = _thread_page(
        NOTICE.replace("亲爱的巫师：", "亲爱的巫师们：", 1)
    ).encode("gb18030")

    def fetch(url: str) -> bytes:
        return pages[url]

    def boom(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        nga.harvest(tmp_path, fetch=fetch, delay=0)

    assert _nga_posts(tmp_path).read_bytes() == before
    leftovers = list((tmp_path / "corpus" / "nga").glob("*.tmp"))
    assert leftovers == []


def test_cli_nga_passes_safety_flags(monkeypatch):
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return {
            "entry_count": 0,
            "post_count": 0,
            "selected": 0,
            "boundary": "all",
            "changed": False,
        }

    monkeypatch.setattr(cli.nga, "harvest", fake)
    assert (
        cli.main(
            [
                "nga",
                "--before",
                "2022-01-26",
                "--limit",
                "5",
                "--out",
                "smoke",
                "--allow-shrink",
            ]
        )
        == 0
    )
    assert captured["before"] == "2022-01-26"
    assert captured["limit"] == 5
    assert captured["out"] == "smoke"
    assert captured["allow_shrink"] is True
