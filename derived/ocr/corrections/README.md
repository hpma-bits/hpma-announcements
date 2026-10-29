# OCR correction ledger

One JSON file per corrected notice, named by the Dashen feed id. Each entry
locates one OCR block and records the accepted source-image reading:

```json
{
  "image_index": 2,
  "block_index": 23,
  "original": "（注：只要魔咒组合中存在“阿瓦达索命”、“钻心骨”等不可饶恕咒，",
  "corrected": "（注：只要魔咒组合中存在“阿瓦达索命”、“钻心剜骨”等不可饶恕咒，",
  "reason": "漏字“剜”；咒语名源图为“钻心剜骨”",
  "confidence": "high",
  "recrop": "python tools/ocr_review.py crop dashen.620b75eec3e1e50001f33764 2 23"
}
```

- `original` must equal the raw OCR block text exactly.
- `corrected` is the full block text as seen in the source image.
- `recrop` regenerates the evidence crop from the committed image. The crop is
  scratch data (`.review/` is gitignored); the locator and raw image are the
  durable evidence.
- `verified_by` on the file records that a reviewer checked every block against
  the source image. Blanket corpus substitutions alone are not accepted.

The source image is authoritative, not the clean corpus. Known traps:

- `骆吾` is what the images say; the native body text uses `驺吾`. Keep 骆吾.
- `金锻` appears in some images; other notices use `金缎`. Keep each as seen.
- `噢幻草` is what the image says even though the corpus uses `嗅幻草`.
- Source-level typos (`子立`, `银辉之匙`, `蛋祸`, …) are preserved; OCR is only
  restored to the glyphs actually printed.

`python tools/ocr_review.py verify` checks every ledger against the raw OCR and
fails on stale originals. `python tools/ocr_review.py apply` writes the
corrected layer under `derived/ocr/corrected/dashen/`; it is idempotent and
never edits `derived/ocr/dashen/`.
