import json

from tools import apply_verdicts, cli


def _block(text: str) -> dict:
    return {
        "box": [[10, 10], [90, 10], [90, 40], [10, 40]],
        "text": text,
        "score": 0.9,
    }


def _notice(texts: tuple[str, ...] = ("原始文本", "另一行"), engine: str = "v4") -> dict:
    return {
        "id": "dashen.aaaa",
        "engine": engine,
        "images": [{"index": 1, "blocks": [_block(text) for text in texts]}],
    }


def _row(**overrides) -> dict:
    row = {
        "notice": "aaaa",
        "image_index": 1,
        "v4_block": 0,
        "v6_block": 0,
        "kind": "differ",
        "verdict": "correct",
        "corrected": "修正文本",
        "confidence": "high",
        "reason": "测试",
    }
    row.update(overrides)
    return row


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed(
    root,
    rows: list[dict],
    *,
    ocr: bool = True,
    ledger: dict | None = None,
) -> None:
    if ocr:
        _write_json(root / "derived/ocr/dashen/aaaa.json", _notice())
    if ledger is not None:
        _write_json(root / "corrections/dashen/aaaa.json", ledger)
    _write_jsonl(root / "audit/verdicts-test.jsonl", rows)


def _ledger(root) -> dict:
    return json.loads(
        (root / "corrections/dashen/aaaa.json").read_text(encoding="utf-8")
    )


def test_applies_a_correction_from_tracked_audit(tmp_path, capsys):
    _seed(tmp_path, [_row()])

    assert apply_verdicts.run(tmp_path) == 0

    entry = _ledger(tmp_path)["corrections"][0]
    assert entry["image_index"] == 1
    assert entry["block_index"] == 0
    assert entry["original"] == "原始文本"
    assert entry["corrected"] == "修正文本"
    assert entry["via"] == "audit-adjudication"
    assert "crop dashen.aaaa 1 0" in entry["recrop"]
    assert '"applied": 1' in capsys.readouterr().out
    assert not list((tmp_path / "corrections/dashen").glob(".*.tmp"))


def test_dry_run_reports_without_writing(tmp_path, capsys):
    _seed(tmp_path, [_row()])

    assert apply_verdicts.run(tmp_path, dry_run=True) == 0

    assert not (tmp_path / "corrections/dashen/aaaa.json").exists()
    out = capsys.readouterr().out
    assert '"applied": 1' in out
    assert '"ledgers": 1' in out


def test_out_of_range_locator_is_skipped_but_the_rest_applies(tmp_path, capsys):
    _seed(tmp_path, [_row(image_index=9), _row()])

    assert apply_verdicts.run(tmp_path) == 0

    assert len(_ledger(tmp_path)["corrections"]) == 1
    captured = capsys.readouterr()
    assert "image_index 9 out of range" in captured.err
    assert '"invalid": 1' in captured.out


def test_stale_verdict_text_is_skipped(tmp_path, capsys):
    _seed(tmp_path, [_row(v4_text="已经变了的文本")])

    assert apply_verdicts.run(tmp_path) == 0

    assert not (tmp_path / "corrections/dashen/aaaa.json").exists()
    captured = capsys.readouterr()
    assert "verdict text no longer matches OCR" in captured.err
    assert '"invalid": 1' in captured.out


def test_drop_verdict_becomes_a_drop_entry(tmp_path):
    _seed(tmp_path, [_row(verdict="drop", corrected=None)])

    assert apply_verdicts.run(tmp_path) == 0

    entry = _ledger(tmp_path)["corrections"][0]
    assert entry["drop"] is True
    assert entry["original"] == "原始文本"
    assert "corrected" not in entry


def test_rerun_is_idempotent(tmp_path, capsys):
    _seed(tmp_path, [_row()])
    assert apply_verdicts.run(tmp_path) == 0
    path = tmp_path / "corrections/dashen/aaaa.json"
    before = path.read_bytes()

    assert apply_verdicts.run(tmp_path) == 0

    assert path.read_bytes() == before
    assert '"skipped_present": 1' in capsys.readouterr().out


def test_malformed_line_does_not_abort_the_run(tmp_path, capsys):
    _seed(tmp_path, [_row()])
    path = tmp_path / "audit/verdicts-test.jsonl"
    path.write_text("{not json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    assert apply_verdicts.run(tmp_path) == 0

    assert len(_ledger(tmp_path)["corrections"]) == 1
    out = capsys.readouterr()
    assert "invalid JSON" in out.err
    assert '"applied": 1' in out.out


def test_missing_ocr_is_reported_not_crashed(tmp_path, capsys):
    _seed(tmp_path, [_row()], ocr=False)

    assert apply_verdicts.run(tmp_path) == 0

    assert not (tmp_path / "corrections/dashen/aaaa.json").exists()
    assert "cannot read OCR" in capsys.readouterr().err


def test_existing_locator_is_left_untouched(tmp_path, capsys):
    ledger = {
        "id": "dashen.aaaa",
        "corrections": [
            {
                "image_index": 1,
                "block_index": 0,
                "original": "原始文本",
                "corrected": "人工结果",
            }
        ],
    }
    _seed(tmp_path, [_row()], ledger=ledger)
    before = (tmp_path / "corrections/dashen/aaaa.json").read_bytes()

    assert apply_verdicts.run(tmp_path) == 0

    assert (tmp_path / "corrections/dashen/aaaa.json").read_bytes() == before
    assert '"skipped_present": 1' in capsys.readouterr().out


def test_cli_registers_apply_verdicts(monkeypatch):
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli.apply_verdicts, "run", fake_run)

    assert cli.main(["apply-verdicts", "--dry-run"]) == 0
    assert captured == {"dry_run": True}
