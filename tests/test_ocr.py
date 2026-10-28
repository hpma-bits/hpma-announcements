import json

from collectors import ocr


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"pretended-jpeg-body"


def _seed(root, posts, labels):
    import os

    os.makedirs(root / "corpus" / "dashen", exist_ok=True)
    os.makedirs(root / "study", exist_ok=True)
    (root / "corpus" / "dashen" / "posts.jsonl").write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in posts), encoding="utf-8"
    )
    (root / "study" / "labels.jsonl").write_text(
        "".join(json.dumps({"id": pid, "label": lab}) + "\n" for pid, lab in labels), encoding="utf-8"
    )


def _post(post_id, label, urls):
    return {
        "id": post_id,
        "type": 2,
        "published_at": "2026-10-26T18:56:00+08:00",
        "title": "【10月28日更新公告】",
        "text": "维护窗口",
        "image_urls": urls,
        "feed_url": f"https://ds.163.com/feed/{post_id}/",
    }, label


def test_ocr_pipeline_downloads_ocres_and_is_idempotent(tmp_path):
    post_a, _ = _post("aaaa", "update", ["https://ok.166.net/a1", "https://ok.166.net/a2"])
    post_b, _ = _post("bbbb", "event", ["https://ok.166.net/b1"])
    _seed(tmp_path, [post_a, post_b], [("aaaa", "update"), ("bbbb", "event")])

    fetches: list[str] = []
    ocr_calls: list[str] = []

    def fetch(url: str) -> bytes:
        fetches.append(url)
        if url.endswith("a2"):
            raise RuntimeError("boom")
        return JPEG_BYTES

    def engine(path: str):
        ocr_calls.append(path)
        return [{"box": [[0, 0], [9, 0], [9, 9], [0, 9]], "text": "识别文本", "score": 0.97}]

    stats = ocr.run(tmp_path, engine=engine, engine_meta={"name": "fake", "version": "0"}, fetch=fetch, delay=0)

    assert stats["posts"] == 1
    assert stats["images"] == 2
    assert stats["downloaded"] == 1
    assert stats["ocred"] == 1
    assert stats["failed_images"] == 1
    assert stats["written"] == 1

    out = tmp_path / "derived" / "ocr" / "dashen" / "aaaa.json"
    notice = json.loads(out.read_text(encoding="utf-8"))
    assert notice["id"] == "dashen.aaaa"
    assert notice["text"] == "识别文本"
    assert notice["images"][0]["text"] == "识别文本"
    assert notice["images"][0]["path"] == "assets/dashen/aaaa/01.jpg"
    assert notice["images"][1]["error"] == "RuntimeError('boom')"
    assert (tmp_path / "assets" / "dashen" / "aaaa" / "01.jpg").exists()
    assert not (tmp_path / "derived" / "ocr" / "dashen" / "bbbb.json").exists()

    # Rerun: no downloads of healthy images, no OCR, no rewrite — the cached
    # sha256 text is reused. The failed image is retried by design.
    before = out.read_text(encoding="utf-8")
    stats2 = ocr.run(tmp_path, engine=engine, engine_meta={"name": "fake", "version": "0"}, fetch=fetch, delay=0)
    assert stats2["downloaded"] == 0
    assert stats2["ocred"] == 0
    assert stats2["written"] == 0
    assert out.read_text(encoding="utf-8") == before
    assert len(fetches) == 3  # a2 retried once, failed again


def test_ocr_retries_failed_image_on_next_run(tmp_path):
    post, _ = _post("cccc", "update", ["https://ok.166.net/c1"])
    _seed(tmp_path, [post], [("cccc", "update")])

    calls = {"n": 0}

    def engine(path: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("engine hiccup")
        return [{"box": [[0, 0], [9, 0], [9, 9], [0, 9]], "text": "好文本", "score": 0.9}]

    def fetch(url: str) -> bytes:
        return JPEG_BYTES

    first = ocr.run(tmp_path, engine=engine, engine_meta={"name": "fake", "version": "0"}, fetch=fetch, delay=0)
    assert first["failed_images"] == 1

    second = ocr.run(tmp_path, engine=engine, engine_meta={"name": "fake", "version": "0"}, fetch=fetch, delay=0)
    assert second["ocred"] == 1
    assert second["written"] == 1
    notice = json.loads((tmp_path / "derived" / "ocr" / "dashen" / "cccc.json").read_text(encoding="utf-8"))
    assert notice["text"] == "好文本"
    assert "error" not in notice["images"][0]


def test_sniff_ext():
    assert ocr.sniff_ext(b"\xff\xd8\xff\xe0xx") == ".jpg"
    assert ocr.sniff_ext(b"\x89PNG\r\n\x1a\n" + b"xx") == ".png"
    assert ocr.sniff_ext(b"whatever") == ".bin"


def test_tiles_cover_the_full_height_with_overlap():
    assert ocr.tiles(800) == [(0, 800)]
    spans = ocr.tiles(3000, window=1280, step=1024)
    assert spans[0][0] == 0
    assert spans[-1][1] == 3000
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert a1 > b0  # consecutive windows overlap
        assert b0 > a0  # and always advance


def test_tiles_folds_a_tiny_tail_into_the_previous_window():
    spans = ocr.tiles(2110, window=1280, step=1024)
    assert spans == [(0, 1280), (1024, 2110)]
    assert ocr.tiles(2200, window=1280, step=1024) == [(0, 1280), (1024, 2200), (2048, 2200)]


def test_merge_lines_dedupes_overlap_and_keeps_reading_order():
    lines = [
        (110.0, "同一行", 0.9, [[0, 100], [9, 100], [9, 120], [0, 120]]),
        (110.0, "同一行", 0.9, [[0, 100], [9, 100], [9, 120], [0, 120]]),  # overlap duplicate
        (30.0, "第一行", 0.95, [[0, 20], [9, 20], [9, 40], [0, 40]]),
        (500.0, "同一行", 0.9, [[0, 490], [9, 490], [9, 510], [0, 510]]),  # distant repeat is real
        (200.0, "第三行", 0.8, [[0, 190], [9, 190], [9, 210], [0, 210]]),
    ]
    blocks = ocr.merge_lines(lines)
    assert [b["text"] for b in blocks] == ["第一行", "同一行", "第三行", "同一行"]
    assert blocks[0]["score"] == 0.95


def test_engine_meta_change_invalidates_cached_ocr(tmp_path):
    post, _ = _post("dddd", "update", ["https://ok.166.net/d1"])
    _seed(tmp_path, [post], [("dddd", "update")])

    def engine(path: str):
        return [{"box": [[0, 0], [9, 0], [9, 9], [0, 9]], "text": "文本", "score": 0.9}]

    def fetch(url: str) -> bytes:
        return JPEG_BYTES

    ocr.run(tmp_path, engine=engine, engine_meta={"name": "fake", "version": "1"}, fetch=fetch, delay=0)
    stats = ocr.run(tmp_path, engine=engine, engine_meta={"name": "fake", "version": "2"}, fetch=fetch, delay=0)
    assert stats["ocred"] == 1  # re-OCRed because the engine fingerprint changed


def test_image_urls_includes_long_text_inline_images():
    post = {
        "image_urls": ["https://ok.166.net/cover"],
        "long_text": '<p>x</p><img src="https://ok.166.net/inline1"><p>y</p>'
        '<img class="editor-image" src="https://ok.166.net/inline2">'
        '<img src="https://ok.166.net/cover">',
    }
    assert ocr.image_urls_for(post) == [
        "https://ok.166.net/cover",
        "https://ok.166.net/inline1",
        "https://ok.166.net/inline2",
    ]
