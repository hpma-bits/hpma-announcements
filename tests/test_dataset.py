import json

import pytest

from tools import cli, dataset


def _post() -> dict:
    return {
        "id": "aaaa",
        "published_at": "2026-01-01T00:00:00+08:00",
        "title": "【1月1日更新公告】",
        "feed_url": "https://ds.163.com/feed/aaaa/",
    }


def _selection() -> dict:
    return {"id": "aaaa", "label": "update", "notice_date": "2026-01-02"}


def _notice() -> dict:
    return {
        "id": "dashen.aaaa",
        "engine": {"name": "rapidocr-onnxruntime", "version": "1.4.4"},
        "images": [
            {
                "index": 1,
                "path": "assets/dashen/aaaa/01.png",
                "blocks": [
                    {
                        "box": [[10, 10], [90, 10], [90, 40], [10, 40]],
                        "text": "第一行",
                        "score": 0.9,
                    }
                ],
            }
        ],
    }


def _audit(queued: int = 0) -> dict:
    return {
        "id": "dashen.aaaa",
        "engine_record": {"name": "rapidocr-onnxruntime"},
        "engine_audit": {"name": "rapidocr"},
        "counts": {"consensus": 1, "queued": queued},
        "blocks": [],
        "v6_only": [],
        "inserts": [],
    }


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed_dashen(root, *, quality: dict | None = None) -> None:
    _write_jsonl(root / "corpus/dashen/posts.jsonl", [_post()])
    _write_jsonl(root / "study/labels.jsonl", [_selection()])
    _write_json(root / "derived/ocr/dashen/aaaa.json", _notice())
    if quality is not None:
        _write_json(root / "derived/audit/dashen/aaaa.json", quality)


def _seed_nga(root) -> None:
    _write_jsonl(
        root / "corpus/nga/posts.jsonl",
        [{
            "id": "bbbb",
            "published_at": "2025-01-01T00:00:00+08:00",
            "title": "【1月1日更新公告】",
            "text": "正文\r\n第二段",
            "thread_url": "https://nga.178.com/read.php?tid=bbbb",
        }],
    )
    _write_jsonl(
        root / "study/labels-nga.jsonl",
        [{"id": "bbbb", "label": "update", "notice_date": "2025-01-02"}],
    )


def test_verified_quality_is_embedded(tmp_path):
    _seed_dashen(tmp_path, quality=_audit())

    rows = dataset.build(tmp_path)

    assert len(rows) == 1
    assert rows[0]["quality"]["status"] == "verified"
    assert rows[0]["quality"]["queued"] == 0
    assert rows[0]["canonical"] is True


def test_missing_quality_is_refused(tmp_path):
    _seed_dashen(tmp_path)

    with pytest.raises(dataset.DatasetError, match="no audit quality"):
        dataset.build(tmp_path)

    with pytest.raises(dataset.DatasetError, match="no audit quality"):
        dataset.run(tmp_path)


def test_queued_blocks_are_refused(tmp_path):
    _seed_dashen(tmp_path, quality=_audit(queued=2))

    with pytest.raises(dataset.DatasetError, match="2 queued audit blocks"):
        dataset.build(tmp_path)


def test_allow_unverified_publishes_the_row(tmp_path):
    _seed_dashen(tmp_path)

    rows = dataset.build(tmp_path, allow_unverified=True)

    assert len(rows) == 1
    assert "quality" not in rows[0]
    assert dataset.run(tmp_path, check=False, allow_unverified=True) == 0


def test_nga_rows_are_not_gated(tmp_path):
    _seed_nga(tmp_path)

    rows = dataset.build(tmp_path)

    assert len(rows) == 1
    assert rows[0]["source"] == "nga"
    assert "quality" not in rows[0]
    assert rows[0]["text"] == "正文\n第二段"


def test_cli_dataset_forwards_allow_unverified(monkeypatch):
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli.dataset, "run", fake_run)

    assert cli.main(["dataset", "--check", "--allow-unverified"]) == 0
    assert captured == {"check": True, "allow_unverified": True}
