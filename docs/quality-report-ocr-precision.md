# OCR precision report (2026-11-07/08)

Scope: the 162 Dashen update notices (650 images, 27,829 v4 blocks) plus the
733 v6-only extra readings. Goal: every block either agreed by two engines or
adjudicated against its source image, with source fidelity as the rule
(official typos stay).

## Method

1. **Baseline** (tag `pre-ocr-precision`): 819 corrections / 153 notices,
   v4 (rapidocr-onnxruntime 1.4.4, PP-OCRv4) as the engine of record.
2. **Engine benchmark**: 769 human-corrected "hard" blocks - v6 (rapidocr
   3.10, PP-OCRv6 small) matched 74.3% verbatim vs v4's 26.5% (CER 0.087 vs
   0.187), 2x faster on full images. Decision: keep v4 as the record, run v6
   as a second reader over everything, adjudicate every disagreement; new
   notices are OCR'd with v6 from now on (`--force` migrates explicitly).
3. **Known-gap audit**: the 16 confusable-word pairs, glyph by glyph (96
   crops): 32 occurrences are the posts' own inconsistencies (kept), 2 true
   OCR errors fixed.
4. **Punctuation patterns**: 425 stratified crops resolved 4,241 punctuation
   disagreements into class rules (`study/punct-rules.json`): 595 corrections
   applied by rule, 201 by per-notice vote, 2,217 confirmed v4.
5. **Full adjudication**: 6,298 queued items -> differ (893), punct leftovers
   (1,183), v4-only duplicate slices (431), v6-only extras (733), plus the 89
   low-confidence blocks and 61 medium corrections. Everything read as a
   source-image crop; verdicts applied via `apply-verdicts`.

## Result

Universe: 27,829 v4 blocks + 733 v6-only + inserts, tracked per block by
`audit-status`. Final:

| status | blocks |
|---|---|
| consensus | 22,966 |
| corrected (human/ledger) | 1,970 |
| adjudicated | 1,955 |
| punct_rule | 600 |
| watermark (not text) | 653 |
| dropped (duplicate slices) | 512 |
| record (v4-only confirmed) | 16 |
| **queued** | **0** |

Ledger: 3,268 entries over all 162 notices - corrections, drops, inserts
(110 lines the v4 detection missed entirely, including reward-table rows and
watermark-covered tails), and rule applications. Edition and dataset rebuilt;
`verify`, `edition --check`, `dataset --check`, `pytest` green.

## Notable corrections the audit surfaced

- v4 systematically misses line-end punctuation; v6's trailing marks were
  verified real in four independent passes.
- v4 drops single characters at line wraps (鸮/缄/蜷/蹊/鼾/谲/霁 and more).
- Shredded reward/probability tables rebuilt row by row (64762bf1: 49-row
  ladder, 萤火印记 ladder, probability table summing to 100.00).
- Art-font quirks preserved: straight quotes and narrow ! on two banners,
  ”-shaped marks on both sides of one banner, three closing ” after a title.
- 12 lines the v4 detection missed entirely (wrap tails like 吧！/敬上, one
  full sentence) recorded as ledger inserts.

## Residual risk

- Final-line tails under the opaque watermark are unrecoverable; the ledger
  records the legible prefix and marks truncation. Some watermark-covered
  reconstructions carry `confidence: medium`.
- Consensus blocks are agreed by two independent engines but not individually
  crop-read; the prior full human pass covered the v4 side of each pair.
- Source quirks were preserved verbatim, not normalised.
