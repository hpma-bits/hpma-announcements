import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_correction_checkpage", REPO / "tools" / "build_correction_checkpage.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + (13).to_bytes(4, "big")
    + b"IHDR"
    + (640).to_bytes(4, "big")
    + (1234).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
    + b"\x00" * 8
)
BOX_A = [[10, 10], [90, 10], [90, 40], [10, 40]]
BOX_B = [[10, 50], [90, 50], [90, 80], [10, 80]]


def _block(box: list[list[int]], text: str) -> dict:
    return {"box": box, "score": 0.9, "text": text}


def _seed(root: Path) -> None:
    (root / "corpus" / "dashen").mkdir(parents=True)
    (root / "derived" / "ocr" / "dashen").mkdir(parents=True)
    (root / "derived" / "ocr" / "corrected" / "dashen").mkdir(parents=True)
    (root / "derived" / "ocr" / "corrections" / "dashen").mkdir(parents=True)
    asset_dir = root / "assets" / "dashen" / "aaaa"
    asset_dir.mkdir(parents=True)
    (asset_dir / "01.png").write_bytes(PNG)

    post = {
        "id": "aaaa",
        "title": "【1月1日更新公告】",
        "published_at": "2026-01-01T00:00:00+08:00",
        "text": "驺吾降临，巫师们准备追击。",
    }
    (root / "corpus" / "dashen" / "posts.jsonl").write_text(
        json.dumps(post, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    def notice(blocks: list[dict]) -> dict:
        return {
            "id": "dashen.aaaa",
            "source": {"published_at": post["published_at"], "title": post["title"]},
            "images": [
                {
                    "index": 1,
                    "path": "assets/dashen/aaaa/01.png",
                    "text": "\n".join(block["text"] for block in blocks),
                    "blocks": blocks,
                }
            ],
            "text": "\n".join(block["text"] for block in blocks),
        }

    raw = notice([_block(BOX_A, "魔祛觉醒"), _block(BOX_B, "骆吾降临")])
    corrected = notice([_block(BOX_A, "魔法觉醒"), _block(BOX_B, "骆吾降临")])
    (root / "derived/ocr/dashen/aaaa.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    (root / "derived/ocr/corrected/dashen/aaaa.json").write_text(
        json.dumps(corrected, ensure_ascii=False), encoding="utf-8"
    )
    ledger = {
        "id": "dashen.aaaa",
        "corrections": [
            {
                "image_index": 1,
                "block_index": 0,
                "original": "魔祛觉醒",
                "corrected": "魔法觉醒",
                "reason": "祛→法",
                "confidence": "medium",
            }
        ],
    }
    (root / "derived/ocr/corrections/dashen/aaaa.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
    )


def test_build_and_check(tmp_path):
    _seed(tmp_path)
    out_dir = tmp_path / "checkpage"
    payload, warnings, changed = builder.build(tmp_path, out_dir)
    assert not warnings
    assert changed["corrections.html"] and changed["corrections-data.js"]

    term = next(item for item in payload["terms"] if item["id"] == "luowu")
    assert term["sourceBlocks"] == 1
    assert term["sourceNotices"] == 1
    assert term["corpusTarget"] >= 1
    assert term["examples"][0]["width"] == 640
    assert term["examples"][0]["height"] == 1234

    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["original"] == "魔祛觉醒"
    assert item["corrected"] == "魔法觉醒"
    assert "low" in item["categories"]
    assert item["width"] == 640
    assert item["height"] == 1234

    html = (out_dir / "corrections.html").read_text(encoding="utf-8")
    data_js = (out_dir / "corrections-data.js").read_text(encoding="utf-8")
    assert "HPMA 修正复核台" in html
    assert data_js.startswith("window.CORRECTION_REVIEW_DATA=")
    assert "骆吾" in data_js

    _, _, changed_again = builder.build(tmp_path, out_dir)
    assert not any(changed_again.values())
    assert builder.main(["--root", str(tmp_path), "--out", str(out_dir), "--check", "--quiet"]) == 0

    (out_dir / "corrections-data.js").write_text("stale", encoding="utf-8")
    assert builder.main(["--root", str(tmp_path), "--out", str(out_dir), "--check", "--quiet"]) == 1


def test_corpus_evidence_is_bounded():
    hits = builder.corpus_evidence(["a 驺吾 b", "c 驺吾 d", "e 驺吾 f"], "驺吾", limit=2)
    assert hits == ["a 驺吾 b", "c 驺吾 d"]
