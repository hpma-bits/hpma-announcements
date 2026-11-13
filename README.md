# hpma-announcements

Every update notice (更新公告) from *Harry Potter: Magic Awakened*, archived as
data and as readable text. The primary source is the verified Dashen account
`哈利波特魔法觉醒`; the NGA community index thread `tid=27915340` supplies the
operator's plain text for 58 weekly notices from 2021-09-15 to 2022-11-16
(18 before Dashen's first full notice, 40 alongside it). The archive keeps the
source post, its long images as original bytes, OCR of those images, the human
corrections on top of that OCR, and a Markdown edition.

Domain vocabulary lives in [CONTEXT.md](CONTEXT.md); the OCR precision evidence
is in [docs/quality-report-ocr-precision.md](docs/quality-report-ocr-precision.md).

This is an unofficial research and preservation archive. Announcement text and
images remain the property of their rights holders; takedown requests are
honoured. Notices live only on community platforms, where they get edited,
deleted, or lost in redesigns, and the game files do not contain them.

## What is in the archive

```
corpus/dashen/posts.jsonl                   2,324 posts: everything the account published
corpus/nga/posts.jsonl                      58 NGA notices: 18 gap + 40 2022
corpus/nga/index.json                       NGA index snapshot: coverage evidence
corpus/nga/raw/<tid>.html                   first-post HTML fragment (UTF-8)
study/labels.jsonl                          which Dashen posts are notices, and their date
study/labels-nga.jsonl                      which NGA threads are notices
assets/dashen/<feed_id>/<NN>.<ext>          original image bytes (650 images)
derived/ocr/dashen/<feed_id>.json           OCR engine output (v4, the record), immutable; --force is the explicit migration path
derived/ocr-v6/dashen/<feed_id>.json        the v6 second reading (audit input)
corrections/dashen/<id>.json                the correction ledger: human decisions
audit/verdicts-*.jsonl                      adjudicated audit verdicts (primary evidence)
audit/adj-*.jsonl                           the audit review queues (primary evidence)
derived/audit/dashen/<feed_id>.json         per-block audit status (projection of audit/)
edition/dashen/<feed_id>.md                 the readable edition, one file per notice
edition/nga/<tid>.md                        the NGA text-only edition
edition/notices.jsonl                       one line per notice, metadata only
dataset/notices.jsonl                       the website-facing rows: image text + quality
study/punct-rules.json                      the sampled punctuation-pattern decisions
```

220 notices: 162 from Dashen and 58 from NGA. Only those have an edition; the
rest of the Dashen corpus (events, fashion, videos, community posts) is kept
because the selection was derived from it. For 40 dates both sources exist:
the NGA text is the canonical reading and the Dashen OCR row stays as the
image-derived record. `edition/notices.jsonl` marks this with `canonical`.
179 of the 220 rows are canonical: the 40 shared dates pick NGA, and
2022-08-10 carries two Dashen rows plus one NGA row, so the unique-date count
is 179 rather than 180.

A fresh clone is about 350 MB: the 650 original images are committed as-is,
without Git LFS.

`study/` holds the offline evidence for the selection: the labelling prompt,
the evaluation report and the selected ids.

## Reading the edition

Each notice is one Markdown file with YAML front matter and up to two optional
sections:

```markdown
---
id: "dashen.6adf31ceee0069337849bde8"
source: "dashen"
title: "【10月28日更新公告】"
published_at: "2026-10-26T18:56:00+08:00"
notice_date: "2026-10-28"
url: "https://ds.163.com/feed/6adf31ceee0069337849bde8/"
images:
  - "assets/dashen/6adf31ceee0069337849bde8/01.jpg"
---

## 正文

The post's own text.

## 图片文字

### 图 1

One line per OCR block, in reading order.
```

- `published_at` is post time; `notice_date` is the date in the notice title
  (notices are usually posted the evening before).
- `images` are paths relative to the repository root.
- Image text is the engine's reading with the ledger applied: the platform
  watermark is removed, and a line captured twice by overlapping OCR tiles is
  kept once.

NGA notices have `source: "nga"`, `source_author_uid`, `images: []` and no
`## 图片文字`: their text is the repost's own text, not OCR. Their
`published_at` is the NGA post time (usually the evening before the maintenance),
not an official release time. `corpus/nga/posts.jsonl` records the first-post
fragment's `raw_sha256` and the page's `page_sha256` at first capture; reruns
keep the page hash while the fragment is unchanged. When both sources cover the
same date, the NGA row is `canonical: true` and the Dashen row is
`canonical: false`; dates with only Dashen keep Dashen canonical. The full index
of NGA entries, including the 58 test-server entries that are not archived, is
in `corpus/nga/index.json`.

`edition/notices.jsonl` repeats the metadata, one JSON object per notice, oldest
first, for consumers that do not want to parse Markdown.

## Rebuilding

The project is managed with [uv](https://docs.astral.sh/uv/) and a lockfile;
dependencies live in a project-local `.venv`. Plain `uv sync` installs the
base dependencies, which is enough for the tests, `verify` and
`edition --check`; `uv sync --all-extras` adds both OCR engines.

```bash
uv sync --all-extras
uv run python -m collectors.cli corpus     # refresh posts from the read-only Dashen API
uv run python -m collectors.cli nga        # NGA supplement (58 notices; public guest access)
uv run python -m collectors.cli ocr        # download images and OCR them (default v6 engine needs the `audit` extra)
uv run python -m tools.cli verify          # check every ledger entry against the raw OCR
uv run python -m tools.cli edition         # rewrite edition/ from the data
```

Two optional extras carry the OCR engines, which coexist in one venv on a
single `opencv-python-headless` build (the only `cv2` provider: `rapidocr`
3.x requires headless, and the v4 pipeline only needs `cv2.imread` and basic
array ops, no GUI):

- `ocr` — `rapidocr-onnxruntime` 1.4.4, the PP-OCRv4 engine of record that
  produced `derived/ocr/`, plus Pillow for `crop`;
- `audit` — `rapidocr` 3.10.0, the PP-OCRv6 engine used for new notices and as
  the second reader over the historical v4 record.

`collectors.cli ocr` uses the v6 engine for new notices, so it needs the
`audit` extra; the `ocr` extra keeps the v4 engine and provides the Pillow
dependency used by `crop`.

`python -m collectors.cli nga --from-dir <dir>` parses pages saved earlier
instead of touching the network; the directory needs the index page
(`27915340.html` or `thread.html`) and one `<tid>.html` per notice. The collector
uses the page's public `guestJs` cookie, waits five seconds between requests, and
does not log in or download NGA-hosted images.

`python -m tools.cli edition --check` exits non-zero when the committed edition
is not what the data produces. A fresh clone needs neither network access nor an
OCR engine for the tests, `verify` or `edition --check`.

### Checking the environment

```bash
uv run python -m tools.cli doctor --strict --require-ocr --require-audit --ocr-smoke
```

`doctor` reports whether the interpreter is the project `.venv` and the base
dependencies import; the flags above also require each engine to import from
inside the venv with its bundled PP-OCR models present, and `--ocr-smoke`
runs each installed engine on one committed image, fully offline. Any
failure exits 1; without `--strict`, missing optional engines are warnings.

On networks where pypi.org is throttled, point uv at the Tsinghua mirror:

```bash
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync --all-extras
```

## The correction ledger

OCR misreads rare glyphs (驺 as 骆, 嗅 as 噢). Each human decision is one entry
that locates an OCR block and records what the source image shows:

```json
{
  "image_index": 2,
  "block_index": 80,
  "original": "每次完成挑战后可获得高额嗅幻草和基道展特魔法觉醒",
  "corrected": "每次完成挑战后可获得高额嗅幻草和其他道具奖励",
  "reason": "水印窜入与漏识；源图为“其他道具奖励”",
  "confidence": "high",
  "recrop": "python -m tools.cli crop dashen.63bd7308c3e1e500018dba57 2 80"
}
```

- `original` must equal the OCR block text exactly. A ledger that no longer
  matches the OCR is refused, never applied, so corrected text cannot drift.
- A `drop: true` entry is a verdict, not a rewrite: the block is a duplicate
  slice with no line of its own in the source image, and the edition removes
  it instead of replacing its text. Drop entries carry no `corrected`.
- `recrop` regenerates the evidence crop from the committed image with
  `python -m tools.cli crop <notice> <image> <block>`; `crop` needs Pillow from
  the `ocr` extra.
- 3,268 corrections cover all 162 Dashen notices; the block text is the only
  thing the ledger changes. The OCR record is not rewritten except by an
  explicit `--force` engine migration. An `insert` entry records a printed line
  the engine never detected, anchored after a block.

## The cross-engine audit

The 162 committed notices keep their v4 OCR records; an independent v6 reading
(PP-OCRv6 small) lives in `derived/ocr-v6/`. `python -m tools.cli audit-compare`
aligns the two readings image by image (sequence alignment with merge/split
rescue) and queues every disagreement; each queued item was then adjudicated
against its source-image crop - by review agents for the bulk, with pattern
rules where sampling proved a systematic direction:

- Punctuation patterns were sampled across years (425 crops): full-width
   ！？·；－ stay, half-width time colons win, range dashes are full-width －,
   and per-notice conventions (parens/quotes/comma) were voted per notice.
- The 2026-11 audit closed with **zero unreviewed blocks** over 28,672 audit
  units (27,829 v4 blocks + 843 v6-only readings): 22,966 consensus, 1,970
  human corrections, 1,955 adjudicated, 600 punctuation-rule, 653 watermark,
  512 dropped slices, 16 confirmed v4-only lines. `python -m tools.cli
  audit-status` rebuilds the per-block record in `derived/audit/dashen/` and
  fails while anything is queued.

The audit's durable evidence now lives in tracked `audit/`: `verdicts-*.jsonl`
holds each adjudicated verdict, `adj-*.jsonl` the review queues those verdicts
answer. `audit/README.md` documents the schema. The per-block ledger in
`derived/audit/dashen/` is a projection rebuilt from that primary data; the
source-image crops stay in the gitignored `.review/` workspace as scratch.

`python -m tools.cli audit-status --check` rebuilds every ledger in memory and
compares it byte for byte with the committed projection, so a clean clone can
verify the zero-queued claim without `.review/`. Missing evidence is a hard
error and nothing is written; normal writes replace each ledger atomically.

## The dataset

`dataset/notices.jsonl` is the website-facing projection: one row per notice
(220 rows, 179 canonical) with `id / source / canonical / title / notice_date /
published_at / url / source_author_uid / images / text / text_sha256`, plus
per-image `sections` and an audit `quality` summary for Dashen rows. Dashen
`text` is the image text: OCR with the ledger applied (corrections, drops,
inserts), the watermark removed, line breaks preserved; it is not the post's
own body, which lives in the edition's `## 正文` and in
`corpus/dashen/posts.jsonl`. NGA `text` is the repost's own text.
`python -m tools.cli dataset --check` gates staleness in CI.

## Checking

```bash
uv run pytest -q
uv run python -m tools.cli verify
uv run python -m tools.cli audit-status --check
uv run python -m tools.cli edition --check
uv run python -m tools.cli dataset --check
```

CI runs the same commands with uv on a Linux runner without network, OCR
engine or Pillow. `python -m tools.cli doctor` reports whether the local
environment (venv, dependencies, optional OCR engines) is complete; see
"Rebuilding" above. `audit-status --check` reads only committed data and runs
in CI; the write mode (`audit-status`) updates `derived/audit/` locally.

## Known gaps

- The NGA index starts at 2021-09-15 and has no production entry for
  2021-09-29 or 2021-10-06, so those two weeks remain absent.
- NGA covers 2021-09-15 to 2022-11-16; Dashen starts 2022-01-26, so 40 dates
  have both sources. NGA text is a community repost, not an official byte
  source; its row is canonical because it preserves the operator's plain text,
  while the Dashen row keeps the image evidence and OCR.
- The 16 confusable-word pairs were audited glyph by glyph (96 crops): 32
  occurrences are the official posts' own inconsistencies and stay as printed
  (金锻/金缎, 子立, 登陆/登录, 鲁本/鲁伯, 云雾飘渺/缥缈, 凤吟凰鸣/凰吟凤鸣,
  进发出, 换新款, 等级上线, 补获, 无暇信念, 银辉之匙/钥); 2 were true OCR
  errors and are corrected.
- Some final-line tails sit under the platform watermark and are unrecoverable;
  the ledger records the legible prefix and marks the truncation in the reason.
  A handful of watermark-covered reconstructions carry `confidence: medium`.
- The OCR record for the 162 existing notices is the v4 engine; new notices are
  OCR'd with v6 (PP-OCRv6). The two layers are compared by the audit, not mixed
  within one notice.
- The edition covers the update family only. Maintenance, version and
  test-server notices are labelled in `study/labels.jsonl` but have no images,
  OCR or edition yet.

## Source

- Dashen verified account `哈利波特魔法觉醒`, uid `29c10cd0344f462e82e16a516001f0dd`.
  Public read-only GET API, no login and no request signing:
  - list `https://inf.ds.163.com/v1/web/feed/basic/getSomeOneFeeds`
  - detail `https://inf.ds.163.com/v1/web/feed/basic/facade`
  - headers the web client sends: `GL-ClientType: 61`, `GL-DeviceId`,
    `Referer: https://ds.163.com/`
  - Canonical item URL: `https://ds.163.com/feed/<id>/` (type-6 articles use
    `/article/<id>/`).
  - Only this account is fetched, at a low rate, from the JSON API. No page
    scraping, no login bypass; `ds.163.com/robots.txt` disallows `/` for wildcard
    crawlers, so the API host is used instead.
- NGA community index `https://bbs.nga.cn/read.php?tid=27915340`, a curated list
  of 58 production and 58 test-server entries (111 distinct threads; a few cover
  both servers), spanning 2021-09-15 to 2022-11-16. All 58 production entries
  are archived; the test-server entries are indexed but not fetched.
  - Public guest access through the page's own `guestJs` cookie; no login, no
    account, one request every five seconds.
  - Only the first post is archived. Replies and NGA-hosted images are not
    mirrored; the thread URL and author uid stay on every record.
  - The text remains a community repost of the official notice, marked
    `source: "nga"` in the edition and index.
