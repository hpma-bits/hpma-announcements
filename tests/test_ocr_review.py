import importlib.util
import json
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ocr_review", REPO / "tools" / "ocr_review.py")
ocr_review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr_review)

BOX = [[10, 10], [90, 10], [90, 40], [10, 40]]


def notice() -> dict:
    return {
        "id": "dashen.aaaa",
        "family": "update",
        "source": {"published_at": "2026-01-01T00:00:00+08:00", "title": "【1月1日更新公告】"},
        "engine": {},
        "images": [
            {
                "index": 1,
                "path": "assets/dashen/aaaa/01.png",
                "text": "魔祛觉醒\n维护更新公告",
                "blocks": [
                    {"box": BOX, "score": 0.9, "text": "魔祛觉醒"},
                    {"box": [[15, 50], [95, 50], [95, 80], [15, 80]], "score": 0.99, "text": "维护更新公告"},
                ],
            }
        ],
        "text": "魔祛觉醒\n维护更新公告",
    }


def ledger() -> dict:
    return {
        "id": "dashen.aaaa",
        "corrections": [
            {
                "image_index": 1,
                "block_index": 0,
                "original": "魔祛觉醒",
                "corrected": "魔法觉醒",
                "reason": "祛→法",
                "confidence": "high",
            }
        ],
    }


def test_queue_text_lists_locators():
    text = ocr_review.queue_text("aaaa", notice())
    assert "===== NOTICE dashen.aaaa" in text
    assert "IMG 1 | assets/dashen/aaaa/01.png | 2 blocks" in text
    assert "b0000 0.9000 | 魔祛觉醒" in text


def test_block_box_and_same_line():
    first, second = notice()["images"][0]["blocks"]
    assert ocr_review.block_box(first) == (10, 10, 90, 40)
    own_line = {"box": [[12, 12], [80, 12], [80, 38], [12, 38]]}
    assert ocr_review.same_line(first, own_line)
    assert not ocr_review.same_line(first, second)


def test_check_ledger_accepts_matching_original():
    assert ocr_review.check_ledger(notice(), ledger()) == []


def test_check_ledger_rejects_stale_original():
    stale = ledger()
    stale["corrections"][0]["original"] = "魔祛觉配"
    problems = ocr_review.check_ledger(notice(), stale)
    assert any("original mismatch" in problem for problem in problems)


def test_check_ledger_rejects_duplicate_locator():
    duplicate = ledger()
    duplicate["corrections"].append(dict(duplicate["corrections"][0]))
    problems = ocr_review.check_ledger(notice(), duplicate)
    assert any("duplicate locator" in problem for problem in problems)


def test_apply_materialises_corrected_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_review, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ocr_review, "OCR_DIR", Path("derived/ocr/dashen"))
    monkeypatch.setattr(ocr_review, "CORRECTED_DIR", Path("derived/ocr/corrected/dashen"))

    ocr_path = tmp_path / "derived/ocr/dashen/aaaa.json"
    ocr_path.parent.mkdir(parents=True)
    ocr_path.write_text(json.dumps(notice(), ensure_ascii=False), encoding="utf-8")
    raw_before = ocr_path.read_bytes()

    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / "aaaa.json").write_text(json.dumps(ledger(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "corrected"

    code = ocr_review.cmd_apply(Namespace(ledger=str(ledger_dir), out=str(out)))
    assert code == 0

    corrected = json.loads((out / "aaaa.json").read_text(encoding="utf-8"))
    assert corrected["images"][0]["blocks"][0]["text"] == "魔法觉醒"
    assert corrected["images"][0]["text"] == "魔法觉醒\n维护更新公告"
    assert corrected["text"] == "魔法觉醒\n维护更新公告"
    assert corrected["corrections_applied"] == 1
    assert ocr_path.read_bytes() == raw_before
