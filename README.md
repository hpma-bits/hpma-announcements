# hpma-announcements

Archive of the official *Harry Potter: Magic Awakened* update notices published
on Dashen: native text, long images, provenance, and a deterministic index.
Built as the data base for a future searchable text edition.

This is an unofficial research and preservation archive. All announcement text
and images remain the property of their rights holders. See
[docs/intent.md](docs/intent.md) and
[docs/adr/0001-public-archive-content-policy.md](docs/adr/0001-public-archive-content-policy.md).

## Reviewing the OCR layer

The OCR output is checked in a static, offline review page. Build it from the
committed corpus, labels, OCR JSON and images, then open `checkpage/index.html`
directly — no server needed; images are read from the checkout:

```
python tools/build_checkpage.py
```

Each notice shows its original images next to the OCR lines. Hovering a text
line highlights its box on the image, and clicking either side scrolls the
other into view. Lines below the confidence thresholds are tinted. The
reviewer can mark a notice 通过 / 有问题, flag individual images, leave notes
and correct OCR text in place.

Review state lives in the browser's localStorage. **导出核对结果** downloads a
`checkpage-review-*.json` with status, notes, image flags and corrected text;
**导入记录** merges such a file back. Corrections are review data, not a
rewrite of `derived/ocr/` — applying them is a separate step.

`python tools/build_checkpage.py --check` exits non-zero when the generated
page is stale; the test suite uses it to prove the build is deterministic.

## Corrected OCR layer

`derived/ocr/dashen/` remains the immutable engine output. Verified text
corrections are stored beside it:

- `derived/ocr/corrections/dashen/<feed_id>.json` — one ledger per notice.
  Every entry locates a block by `(image_index, block_index)`, keeps the raw
  `original` and the source-image `corrected` text, a reason and a reproducible
  `recrop` command.
- `derived/ocr/corrected/dashen/<feed_id>.json` — the notice with accepted
  corrections applied and its per-image and notice-level `text` rebuilt. Only
  notices with accepted corrections have a file here; the remaining notices
  are unchanged raw OCR. The raw OCR files are never modified.

`tools/ocr_review.py` is the review pipeline: `queue` (compact text per
notice), `batches` (balanced reviewer batches), `candidates` (low-confidence
blocks, optionally per batch), `crop` (upscaled source-image crop for one
block), `sheet` (labelled contact sheets), `term`/`grep` (corpus and OCR
lookup), `verify` and `apply`. `.review/` holds gitignored scratch data: queues,
crops, sheets and reviewer proposals.

The current pass accepted 769 corrections across 153 of 162 notices. Every
corrected block was checked against a crop of its source image; repeated
systematic variants (for example 魔祛觉醒 → 魔法觉醒) were sampled across
notices. Two source-level variants are deliberately preserved: 骆吾 and 金锻,
because the images use those forms even where the native body text does not.
Some corrections also remove the repeated `大神@哈利波特魔法觉醒` watermark
suffix from OCR lines that were merged with it; the ledger reason marks those.

## Reviewing the corrections

The correction ledger has its own focused review page, separate from the full
OCR checkpage. Build it and open `checkpage/corrections.html` directly:

```
python tools/build_correction_checkpage.py
```

The page has three review layers:

- **术语对照** — where the source images and the clean corpus disagree:
  骆吾/驺吾, 金锻/金缎, 石墩出动/石礅出动, 噢幻草/嗅幻草, 银辉之匙/钥,
  戈德利克/戈德里克, 鲁本/鲁伯, 长跑/长袍, and the smaller one-off variants.
  Each card shows representative source-image crops, the corpus counts and
  corpus snippets, and a recommendation; the reviewer chooses 保留源图,
  按 corpus, or another form.
- **低置信 / 水印重建 / 整行重写** — the 61 `medium` corrections, the 86
  watermark reconstructions and the 21 full-line rewrites, each with its
  source-image crop, OCR/fix diff, reason and the notice's clean corpus body.
- **全部修正** — all 769 accepted corrections, searchable.

The clean corpus is shown as the reference, but the source image remains the
evidence for what the notice actually printed. Decisions live in the browser's
localStorage; **导出复核结果** downloads a JSON with the term/item decisions,
snapshots of the original and corrected text, and notes. **导入记录** merges it
back. Applying the decisions to the corrected layer is a separate step.
`python tools/build_correction_checkpage.py --check` exits non-zero when the
generated page is stale.
