import contextlib
import inspect
import json
import threading
import urllib.error
import urllib.request

import pytest

from tools import review, review_app


def load_fixture():
    return json.loads(review_app.FIXTURE_PATH.read_text(encoding="utf-8"))


def issue(issue_id, issue_type="term_conflict", severity="high", **extra):
    value = {
        "id": issue_id,
        "type": issue_type,
        "severity": severity,
        "title": issue_id,
        "options": [{"value": "defer", "label": "稍后"}],
    }
    value.update(extra)
    return value


def decision(issue_id, value, decided_at="2026-11-13T10:00:00+00:00", **extra):
    record = {"issue_id": issue_id, "decision": value, "decided_at": decided_at}
    record.update(extra)
    return record


@contextlib.contextmanager
def running_server():
    server = review_app.make_server(0, "127.0.0.1", force_fixture=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.headers.get_content_type(), response.read()


def post(url, body):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.headers.get_content_type(), response.read()


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_page_is_one_self_contained_document():
    html = review_app.PAGE_HTML
    assert review_app.PAGE_TITLE in html
    assert "<title>文本裁决台" in html
    assert "<style>" in html
    assert "<script>" in html
    assert "/api/queue" in html
    assert "/api/decisions" in html
    assert "/api/crop" in html
    assert "/api/notice" in html
    assert "max-width: 1200px" in html
    assert "一次只处理一个文本问题" in html


def test_page_defaults_to_the_term_view_and_understands_the_new_fields():
    html = review_app.PAGE_HTML
    assert 'filter: "term"' in html
    assert "source_damage" in html
    assert "match_note" in html
    assert "源图损坏" in html


def test_page_has_no_external_assets():
    html = review_app.PAGE_HTML
    assert "<link " not in html
    assert 'src="http' not in html
    assert "src='http" not in html
    assert "@import" not in html


def test_page_does_not_embed_the_queue():
    html = review_app.PAGE_HTML
    assert "金锻" not in html
    assert "62dfbea2c3e1e5000120a501" not in html
    assert "term:金锻|金缎" not in html


def test_page_mentions_keyboard_and_decision_controls():
    html = review_app.PAGE_HTML
    for phrase in ("保存并下一题", "稍后", "上一题", "键盘", "Enter", "仅供参考", "为什么需要你"):
        assert phrase in html
    assert "same_alias" in html or "preferred" in html
    assert "override" in html


def test_page_renders_archived_issues_as_read_only():
    html = review_app.PAGE_HTML
    assert "已应用 / 归档" in html
    assert "issue.archived" in html
    assert 'archived ? "" : decisionBlock(issue)' in html
    assert "这条裁决已应用并归档，只读" in html


def test_server_entrypoint_defaults_match_the_spec():
    parameters = inspect.signature(review_app.serve).parameters
    assert parameters["port"].default == 8767
    assert parameters["open_browser"].default is False


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def test_fixture_has_a_term_and_an_ocr_issue():
    data = load_fixture()
    types = [item["type"] for item in data["issues"]]
    assert "term_conflict" in types
    assert "ocr_block" in types

    term = next(item for item in data["issues"] if item["type"] == "term_conflict")
    assert term["id"] == "term:金锻|金缎"
    assert len(term["variants"]) == 2
    assert {variant["text"] for variant in term["variants"]} == {"金锻", "金缎"}
    assert term["impact"]["search_gain"] == 5
    assert "金锻/金缎" in term["match_note"]
    assert len([item for item in term["contexts"] if item["variant"] == "金锻"]) == 4
    assert [option["value"] for option in term["options"]] == [
        "same_alias",
        "different",
        "ocr_error",
        "defer",
    ]
    assert "仅供参考" in term["recommendation"]

    ocr = next(item for item in data["issues"] if item["type"] == "ocr_block")
    assert ocr["severity"] == "medium"
    assert ocr["risk"] == "source_damage"
    assert ocr["crop"] == {"notice": ocr["notice"], "image": 2, "block": 17, "scale": 2}
    assert ocr["original"]
    assert ocr["corrected"]
    assert len(ocr["context_before"]) == 2
    assert len(ocr["context_after"]) == 2
    assert [option["value"] for option in ocr["options"]] == [
        "accept",
        "override",
        "keep_original",
        "source_issue",
        "drop",
        "defer",
    ]


def test_fixture_decisions_is_a_list():
    assert load_fixture()["decisions"] == []


# ---------------------------------------------------------------------------
# Pure queue helpers
# ---------------------------------------------------------------------------


def test_normalize_decisions_keeps_latest_record():
    records = [
        decision("term:a", "defer", "2026-11-13T09:00:00+00:00"),
        decision("term:a", "same_alias", "2026-11-13T10:00:00+00:00", preferred="B"),
        decision("term:b", "different", "2026-11-13T08:00:00+00:00"),
    ]
    latest = review_app.normalize_decisions(records)
    assert latest["term:a"]["decision"] == "same_alias"
    assert latest["term:a"]["preferred"] == "B"
    assert latest["term:b"]["decision"] == "different"


def test_normalize_decisions_accepts_a_mapping():
    latest = review_app.normalize_decisions(
        {"ocr:x": {"decision": "accept", "decided_at": "2026-11-13T10:00:00+00:00"}}
    )
    assert latest["ocr:x"]["issue_id"] == "ocr:x"
    assert latest["ocr:x"]["decision"] == "accept"


def test_decorate_issues_sets_status_and_puts_pending_first():
    issues = [
        issue("ocr:1", "ocr_block", "low"),
        issue("term:1", "term_conflict", "high"),
        issue("term:2", "term_conflict", "high"),
        issue("ocr:archived", "ocr_block", "medium", archived=True, applied=True, options=[]),
    ]
    decisions = {
        "ocr:1": decision("ocr:1", "accept"),
        "term:2": decision("term:2", "defer"),
        "ocr:archived": decision("ocr:archived", "override"),
    }
    decorated = review_app.decorate_issues(issues, decisions)
    assert [item["id"] for item in decorated] == [
        "term:1",
        "term:2",
        "ocr:1",
        "ocr:archived",
    ]
    statuses = {item["id"]: item["status"] for item in decorated}
    assert statuses == {
        "term:1": "pending",
        "ocr:1": "decided",
        "term:2": "deferred",
        "ocr:archived": "decided",
    }
    assert decorated[2]["decision"]["decision"] == "accept"
    assert decorated[0]["decision"] is None
    assert decorated[3]["archived"] is True
    assert decorated[3]["applied"] is True


def test_decorate_issues_preserves_the_data_layer_order():
    issues = [
        issue("term:1", "term_conflict", "medium"),
        issue("ocr:damage", "ocr_block", "medium", risk="source_damage"),
        issue("ocr:style", "ocr_block", "medium", risk="style"),
        issue("ocr:other", "ocr_block", "medium", risk="other"),
    ]
    decorated = review_app.decorate_issues(issues, {})
    assert [item["id"] for item in decorated] == [
        "term:1",
        "ocr:damage",
        "ocr:style",
        "ocr:other",
    ]


def test_compute_stats_counts_pending_high_and_decided():
    issues = [
        issue("term:1", "term_conflict", "high"),
        issue("ocr:1", "ocr_block", "high"),
        issue("ocr:2", "ocr_block", "medium"),
    ]
    decisions = {"ocr:1": decision("ocr:1", "accept")}
    decorated = review_app.decorate_issues(issues, decisions)
    stats = review_app.compute_stats(decorated)
    assert stats["total"] == 3
    assert stats["pending"] == 2
    assert stats["high"] == 1
    assert stats["decided"] == 1
    assert stats["progress"] == pytest.approx(1 / 3)


def test_repo_queue_keeps_applied_overrides_in_the_decided_view():
    queue = review.build_queue()
    stats = queue["stats"]
    assert (stats["total"], stats["pending"], stats["resolved"]) == (97, 0, 97)
    assert stats["term"] == 13
    assert stats["ocr"] == 84
    assert stats["high_total"] == 6

    archived = [item for item in queue["issues"] if item.get("archived")]
    assert len(archived) == 33
    assert all(item["applied"] for item in archived)
    assert all(item["confidence"] == "high" for item in archived)
    assert all(item["options"] == [] for item in archived)

    decorated = review_app.decorate_issues(queue["issues"], review.load_decisions())
    assert len(review_app.filter_issues(decorated, "decided")) == 97
    assert review_app.compute_stats(decorated)["decided"] == 97


def test_filter_issues_covers_every_ui_chip():
    issues = [
        issue("term:1", "term_conflict", "high"),
        issue("ocr:1", "ocr_block", "high"),
        issue("ocr:2", "ocr_block", "low"),
    ]
    decisions = {"ocr:2": decision("ocr:2", "drop")}
    decorated = review_app.decorate_issues(issues, decisions)
    assert len(review_app.filter_issues(decorated, "all")) == 3
    assert [item["id"] for item in review_app.filter_issues(decorated, "term")] == ["term:1"]
    assert [item["id"] for item in review_app.filter_issues(decorated, "ocr")] == ["ocr:1", "ocr:2"]
    assert [item["id"] for item in review_app.filter_issues(decorated, "high")] == [
        "term:1",
        "ocr:1",
    ]
    assert [item["id"] for item in review_app.filter_issues(decorated, "pending")] == [
        "term:1",
        "ocr:1",
    ]
    assert [item["id"] for item in review_app.filter_issues(decorated, "decided")] == ["ocr:2"]
    assert len(review_app.filter_issues(decorated, "unknown")) == 3


# ---------------------------------------------------------------------------
# Crop/notice parameter helpers
# ---------------------------------------------------------------------------


def test_parse_crop_params_applies_defaults():
    spec = review_app.parse_crop_params("notice=dashen.abc123&image=2&block=17")
    assert spec.notice == "abc123"
    assert (spec.image, spec.block) == (2, 17)
    assert spec.scale == 2.0
    assert spec.context == 0


def test_parse_crop_params_accepts_the_fixture_crop():
    ocr = next(item for item in load_fixture()["issues"] if item["type"] == "ocr_block")
    crop = ocr["crop"]
    spec = review_app.parse_crop_params(
        f"notice={crop['notice']}&image={crop['image']}&block={crop['block']}"
        f"&scale={crop['scale']}"
    )
    assert spec.notice == "62dfbea2c3e1e5000120a501"
    assert spec.scale == 2.0


@pytest.mark.parametrize(
    "query",
    [
        "",
        "image=2&block=3",
        "notice=dashen.abc&image=0&block=3",
        "notice=dashen.abc&image=2&block=-1",
        "notice=dashen.abc&image=nope&block=3",
        "notice=dashen.abc&image=2&block=3&scale=9",
        "notice=dashen.abc&image=2&block=3&context=-1",
        "notice=nga.123&image=1&block=0",
        "notice=dashen.../etc&image=1&block=0",
    ],
)
def test_parse_crop_params_rejects_bad_requests(query):
    with pytest.raises(review_app.BadRequest):
        review_app.parse_crop_params(query)


def test_notice_path_resolves_the_edition_file():
    path = review_app.notice_path("dashen.6203c405c5a325000127d995")
    assert path == review_app.REPO_ROOT / "edition/dashen/6203c405c5a325000127d995.md"
    nga = review_app.notice_path("nga.28502179")
    assert nga == review_app.REPO_ROOT / "edition/nga/28502179.md"


@pytest.mark.parametrize("notice_id", ["", "dashen.../etc/passwd", "dashen.", "dashen.a/b"])
def test_notice_path_rejects_bad_ids(notice_id):
    with pytest.raises(review_app.BadRequest):
        review_app.notice_path(notice_id)


def test_read_notice_returns_markdown():
    text = review_app.read_notice("dashen.6203c405c5a325000127d995")
    assert text.startswith("---")
    assert "notice_date" in text


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_store_falls_back_to_the_fixture_and_keeps_decisions_in_memory():
    store = review_app.ReviewStore(force_fixture=True)
    queue = store.queue()
    assert queue["source"] == "fixture"
    assert len(queue["issues"]) == 2
    assert queue["stats"]["pending"] == 2
    assert queue["stats"]["high"] == 1
    assert queue["decisions"] == {}

    saved = store.save_decision(
        "term:金锻|金缎",
        "same_alias",
        {"preferred": "金缎", "aliases": ["金锻"]},
        "官方两种写法都出现，检索合并",
    )
    assert saved["preferred"] == "金缎"
    assert saved["aliases"] == ["金锻"]

    after = store.queue()
    assert after["stats"]["decided"] == 1
    assert after["stats"]["pending"] == 1
    assert after["decisions"]["term:金锻|金缎"]["note"].startswith("官方")


def test_store_validates_decisions():
    store = review_app.ReviewStore(force_fixture=True)
    with pytest.raises(review_app.BadRequest):
        store.save_decision("term:金锻|金缎", "nonsense", {}, "")
    with pytest.raises(review_app.BadRequest):
        store.save_decision("term:金锻|金缎", "same_alias", {}, "")
    with pytest.raises(review_app.BadRequest):
        store.save_decision("term:金锻|金缎", "same_alias", {"preferred": "金缎子"}, "")
    with pytest.raises(review_app.BadRequest):
        store.save_decision("ocr:nope", "accept", {}, "")
    with pytest.raises(review_app.ReviewUnavailable):
        store.export()


class FakeBackend:
    def __init__(self):
        self.saved = None

    def build_queue(self):
        return [
            issue(
                "term:fake",
                variants=[{"text": "甲"}, {"text": "乙"}],
                options=[
                    {"value": "same_alias", "label": "合并"},
                    {"value": "defer", "label": "稍后"},
                ],
            )
        ]

    def load_decisions(self):
        return []

    def save_decision(self, issue_id, decision, payload=None, note=""):
        self.saved = (issue_id, decision, payload, note)
        return {"issue_id": issue_id, "decision": decision}

    def export(self):
        return {"paths": ["review/terms.json"]}


def test_store_delegates_to_the_data_layer():
    backend = FakeBackend()
    store = review_app.ReviewStore(backend=backend)
    assert store.queue()["source"] == "review"
    saved = store.save_decision("term:fake", "same_alias", {"preferred": "乙"}, "合并")
    assert saved["decision"] == "same_alias"
    assert backend.saved == ("term:fake", "same_alias", {"preferred": "乙"}, "合并")
    assert store.export() == {"paths": ["review/terms.json"]}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ArchivedBackend(FakeBackend):
    def build_queue(self):
        return [
            issue(
                "ocr:archived",
                "ocr_block",
                "medium",
                archived=True,
                applied=True,
                options=[],
            )
        ]

    def load_decisions(self):
        return [decision("ocr:archived", "override")]


def test_store_refuses_to_save_an_archived_issue():
    backend = ArchivedBackend()
    store = review_app.ReviewStore(backend=backend)
    with pytest.raises(review_app.BadRequest):
        store.save_decision("ocr:archived", "override", {"corrected": "新文本"}, "")


def test_server_serves_the_page_and_fixture_queue():
    with running_server() as base_url:
        status, content_type, body = get(base_url + "/")
        assert status == 200
        assert content_type == "text/html"
        assert "文本裁决台" in body.decode("utf-8")

        status, content_type, body = get(base_url + "/api/queue")
        assert status == 200
        assert content_type == "application/json"
        queue = json.loads(body)
        assert queue["source"] == "fixture"
        assert len(queue["issues"]) == 2
        assert queue["stats"]["pending"] == 2
        assert {item["type"] for item in queue["issues"]} == {"term_conflict", "ocr_block"}


def test_server_saves_a_decision_and_updates_stats():
    with running_server() as base_url:
        status, _, body = post(
            base_url + "/api/decisions",
            {
                "issue_id": "ocr:dashen.62dfbea2c3e1e5000120a501:2:17",
                "decision": "override",
                "payload": {"corrected": "可解锁特权：日常活动中金币获取效率提升50%。"},
                "note": "crop 确认",
            },
        )
        assert status == 200
        result = json.loads(body)
        assert result["ok"] is True
        assert result["decision"]["decision"] == "override"
        assert result["queue"]["stats"]["decided"] == 1
        decided = next(
            item for item in result["queue"]["issues"] if item["type"] == "ocr_block"
        )
        assert decided["status"] == "decided"

        _, _, body = get(base_url + "/api/queue")
        queue = json.loads(body)
        assert queue["stats"]["decided"] == 1
        assert queue["decisions"][decided["id"]]["corrected"].endswith("50%。")

        _, _, body = post(
            base_url + "/api/decisions",
            {
                "issue_id": decided["id"],
                "decision": "defer",
                "payload": {},
                "note": "重新打开，待重新裁决",
            },
        )
        reopened = json.loads(body)["queue"]
        assert reopened["stats"]["decided"] == 0
        assert reopened["stats"]["deferred"] == 1
        again = next(item for item in reopened["issues"] if item["id"] == decided["id"])
        assert again["status"] == "deferred"


def test_server_rejects_a_bad_decision():
    with running_server() as base_url:
        with pytest.raises(urllib.error.HTTPError) as error:
            post(base_url + "/api/decisions", {"issue_id": "term:金锻|金缎", "decision": "same_alias"})
        assert error.value.code == 400
        error.value.close()


def test_server_returns_501_when_export_needs_the_data_layer():
    with running_server() as base_url:
        with pytest.raises(urllib.error.HTTPError) as error:
            post(base_url + "/api/export", {})
        assert error.value.code == 501
        assert "数据层" in json.loads(error.value.read())["error"]
        error.value.close()


def test_server_rejects_a_bad_crop_and_lists_notice_text():
    with running_server() as base_url:
        with pytest.raises(urllib.error.HTTPError) as error:
            get(base_url + "/api/crop?notice=dashen.abc&image=0&block=1")
        assert error.value.code == 400
        error.value.close()

        status, content_type, body = get(
            base_url + "/api/notice?id=dashen.6203c405c5a325000127d995"
        )
        assert status == 200
        assert content_type == "text/plain"
        assert "notice_date" in body.decode("utf-8")

        with pytest.raises(urllib.error.HTTPError) as error:
            get(base_url + "/api/notice?id=dashen.missing")
        assert error.value.code == 404
        error.value.close()
