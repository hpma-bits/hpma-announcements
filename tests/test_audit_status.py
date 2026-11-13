import json
from pathlib import Path

import pytest

from tools import audit_status


def _block(text: str) -> dict:
    return {
        "box": [[10, 10], [90, 10], [90, 40], [10, 40]],
        "text": text,
        "score": 0.9,
    }


def _notice(feed: str, texts: tuple[str, ...], engine: str) -> dict:
    return {
        "id": f"dashen.{feed}",
        "engine": engine,
        "images": [
            {
                "index": 1,
                "path": f"assets/dashen/{feed}/01.png",
                "blocks": [_block(text) for text in texts],
            }
        ],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed(
    root: Path,
    *,
    feed: str = "aaaa",
    v4: tuple[str, ...] = ("一致",),
    v6: tuple[str, ...] | None = ("一致",),
    ledger: dict | None = None,
) -> None:
    _write_json(root / "derived/ocr/dashen" / f"{feed}.json", _notice(feed, v4, "v4"))
    if v6 is not None:
        _write_json(root / "derived/ocr-v6/dashen" / f"{feed}.json", _notice(feed, v6, "v6"))
    if ledger is not None:
        _write_json(root / "corrections/dashen" / f"{feed}.json", ledger)


def _evidence(
    root: Path,
    *,
    verdicts: list[dict] | None = None,
    adj: list[dict] | None = None,
) -> None:
    _write_jsonl(root / "audit/verdicts-test.jsonl", verdicts or [])
    _write_jsonl(root / "audit/adj-test.jsonl", adj or [])


def _payload(root: Path, feed: str = "aaaa") -> dict:
    payloads, _ = audit_status.build(root)
    return json.loads(payloads[feed])


@pytest.mark.parametrize(
    ("op", "verdict", "expected"),
    [
        (None, None, "queued"),
        ({"relation": "equal"}, None, "consensus"),
        ({"relation": "merged"}, None, "consensus"),
        ({"relation": "split"}, None, "consensus"),
        ({"relation": "punct"}, None, "consensus"),
        ({"relation": "differ"}, None, "queued"),
        ({"relation": "v4_only"}, None, "queued"),
        ({"relation": "differ"}, {"verdict": "v4_ok"}, "record"),
        ({"relation": "differ"}, {"verdict": "v4_right"}, "adjudicated"),
        ({"relation": "differ"}, {"verdict": "v4_covered"}, "adjudicated"),
        ({"relation": "differ"}, {"verdict": "source_issue"}, "source_issue"),
        ({"relation": "differ"}, {"verdict": "unheard_of"}, "queued"),
    ],
)
def test_status_priority(op, verdict, expected):
    assert audit_status._status_for_op(op, verdict) == expected


def test_ledger_statuses_outrank_verdicts(tmp_path):
    ledger = {
        "id": "dashen.aaaa",
        "corrections": [
            {"image_index": 1, "block_index": 0, "drop": True},
            {"image_index": 1, "block_index": 2, "original": "人工修正", "corrected": "人工修正"},
            {
                "image_index": 1,
                "block_index": 3,
                "original": "标点规则",
                "corrected": "标点规则",
                "via": "punct-rule",
            },
        ],
    }
    texts = (
        "重复切片",
        "大神@哈利波特魔法觉醒",
        "人工修正",
        "标点规则",
        "v4判定",
        "v4对",
        "一致",
    )
    _seed(tmp_path, v4=texts, v6=texts[2:], ledger=ledger)
    _evidence(
        tmp_path,
        verdicts=[
            {"notice": "aaaa", "image_index": 1, "v4_block": 4, "v6_block": 4, "verdict": "v4_ok"},
            {"notice": "aaaa", "image_index": 1, "v4_block": 5, "v6_block": 5, "verdict": "v4_right"},
        ],
    )

    result = _payload(tmp_path)

    assert result["counts"] == {
        "dropped": 1,
        "watermark": 1,
        "corrected": 1,
        "punct_rule": 1,
        "record": 1,
        "adjudicated": 1,
        "consensus": 1,
    }
    statuses = [block["status"] for block in result["blocks"]]
    assert statuses == [
        "dropped",
        "watermark",
        "corrected",
        "punct_rule",
        "record",
        "adjudicated",
        "consensus",
    ]


def test_missing_v6_reading_is_skipped(tmp_path, capsys):
    _seed(tmp_path, v6=None)
    _evidence(tmp_path)

    payloads, totals = audit_status.build(tmp_path)

    assert payloads == {}
    assert totals == {}
    assert "no v6 reading for aaaa" in capsys.readouterr().err


def test_empty_corpus_is_a_clean_run(tmp_path):
    _evidence(tmp_path)

    payloads, totals = audit_status.build(tmp_path)

    assert payloads == {}
    assert totals == {}
    assert audit_status.run(tmp_path) == 0


def test_missing_evidence_refuses_to_write(tmp_path):
    _seed(tmp_path)

    with pytest.raises(audit_status.MissingEvidenceError, match="verdicts-\\*\\.jsonl"):
        audit_status.run(tmp_path)

    assert not (tmp_path / "derived/audit").exists()


def test_missing_queue_evidence_refuses_to_write(tmp_path):
    _seed(tmp_path)
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit/verdicts-test.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(audit_status.MissingEvidenceError, match="adj-\\*\\.jsonl"):
        audit_status.run(tmp_path)

    assert not (tmp_path / "derived/audit").exists()


def test_check_does_not_create_a_missing_projection(tmp_path):
    _seed(tmp_path)
    _evidence(tmp_path)

    assert audit_status.run(tmp_path, check=True) == 1

    assert not (tmp_path / "derived/audit").exists()


def test_check_writes_nothing_when_current(tmp_path):
    _seed(tmp_path, v4=("一致", "保留"), v6=("一致", "保留"))
    _evidence(tmp_path)
    assert audit_status.run(tmp_path) == 0
    paths = sorted((tmp_path / "derived/audit/dashen").glob("*.json"))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    assert audit_status.run(tmp_path, check=True) == 0

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    assert after == before
    assert not list((tmp_path / "derived/audit/dashen").glob(".*.tmp"))


def test_check_reports_stale_without_rewriting(tmp_path, capsys):
    _seed(tmp_path)
    _evidence(tmp_path)
    assert audit_status.run(tmp_path) == 0
    path = tmp_path / "derived/audit/dashen/aaaa.json"
    path.write_bytes(b"{}\n")

    assert audit_status.run(tmp_path, check=True) == 1

    assert path.read_bytes() == b"{}\n"
    assert "stale derived/audit/dashen/aaaa.json" in capsys.readouterr().err


def test_check_reports_an_orphan_ledger(tmp_path, capsys):
    _seed(tmp_path)
    _evidence(tmp_path)
    assert audit_status.run(tmp_path) == 0
    orphan = tmp_path / "derived/audit/dashen/zzzz.json"
    orphan.write_bytes(b"{}\n")

    assert audit_status.run(tmp_path, check=True) == 1

    assert orphan.exists()
    assert "orphan derived/audit/dashen/zzzz.json" in capsys.readouterr().err


def test_rebuild_is_byte_identical(tmp_path):
    _seed(tmp_path)
    _evidence(tmp_path)
    assert audit_status.run(tmp_path) == 0

    payloads, totals = audit_status.build(tmp_path)

    assert totals == {"consensus": 1}
    for feed, payload in payloads.items():
        assert (tmp_path / "derived/audit/dashen" / f"{feed}.json").read_bytes() == payload


def test_queued_blocks_fail_write_and_check_unless_soft(tmp_path, capsys):
    _seed(tmp_path, v4=("甲",), v6=("乙",))
    _evidence(tmp_path)

    assert audit_status.run(tmp_path, soft=True) == 0
    assert audit_status.run(tmp_path, check=True) == 1
    assert audit_status.run(tmp_path) == 1

    err = capsys.readouterr().err
    assert "1 queued blocks remain" in err


def test_check_and_soft_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        audit_status.run(tmp_path, check=True, soft=True)
