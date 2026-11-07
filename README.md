# hpma-announcements

Every update notice (更新公告) from *Harry Potter: Magic Awakened*, archived as
data and as readable text. The primary source is the verified Dashen account
`哈利波特魔法觉醒`; the NGA community index thread `tid=27915340` supplies the
operator's plain text for 58 weekly notices from 2021-09-15 to 2022-11-16
(18 before Dashen's first full notice, 40 alongside it). The archive keeps the
source post, its long images as original bytes, OCR of those images, the human
corrections on top of that OCR, and a Markdown edition.

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
derived/ocr/dashen/<feed_id>.json           OCR engine output, immutable
derived/ocr/corrections/dashen/<id>.json    the correction ledger: human decisions
edition/dashen/<feed_id>.md                 the readable edition, one file per notice
edition/nga/<tid>.md                        the NGA text-only edition
edition/notices.jsonl                       one line per notice, metadata only
```

220 notices: 162 from Dashen and 58 from NGA. Only those have an edition; the
rest of the Dashen corpus (events, fashion, videos, community posts) is kept
because the selection was derived from it. For 40 dates both sources exist:
the NGA text is the canonical reading and the Dashen OCR row stays as the
image-derived record. `edition/notices.jsonl` marks this with `canonical`.

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
uv run python -m collectors.cli ocr        # download images and OCR them (needs the `ocr` extra)
uv run python -m tools.cli verify          # check every ledger entry against the raw OCR
uv run python -m tools.cli edition         # rewrite edition/ from the data
```

Two optional extras carry the OCR engines, which coexist in one venv on a
single `opencv-python-headless` build (the only `cv2` provider: `rapidocr`
3.x requires headless, and the v4 pipeline only needs `cv2.imread` and basic
array ops, no GUI):

- `ocr` — `rapidocr-onnxruntime` 1.4.4, the PP-OCRv4 engine of record that
  produced `derived/ocr/`;
- `audit` — `rapidocr` 3.10.0, a PP-OCRv6 second reader for spot checks.

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
- `recrop` regenerates the evidence crop from the committed image with
  `python -m tools.cli crop <notice> <image> <block>`.
- 819 corrections cover 153 of the 162 Dashen notices; the block text is the only
  thing the ledger changes, `derived/ocr/` is never edited.

## Checking

```bash
uv run pytest -q
uv run python -m tools.cli verify
uv run python -m tools.cli edition --check
```

CI runs the same three commands with uv on a Linux runner without network, OCR
engine or Pillow. `python -m tools.cli doctor` reports whether the local
environment (venv, dependencies, optional OCR engines) is complete; see
"Rebuilding" above.

## Known gaps

- The NGA index starts at 2021-09-15 and has no production entry for
  2021-09-29 or 2021-10-06, so those two weeks remain absent.
- NGA covers 2021-09-15 to 2022-11-16; Dashen starts 2022-01-26, so 40 dates
  have both sources. NGA text is a community repost, not an official byte
  source; its row is canonical because it preserves the operator's plain text,
  while the Dashen row keeps the image evidence and OCR.
- 16 confusable words in the Dashen OCR are not yet checked glyph by glyph
  against the images: 石墩/石礅, 金锻/金缎, 银辉之匙/银辉之钥, 子立/孑立,
  戈德利克/戈德里克, 鲁本·海格/鲁伯·海格, 长跑专卖店/长袍专卖店,
  云雾飘渺/云雾缥缈, 凤吟凰鸣/凰吟凤鸣, 讽爽英姿/飒爽英姿, 进发出/迸发出,
  换新款/焕新款, 等级上线/等级上限, 补获/捕获, 无暇信念/无瑕信念, 登陆/登录.
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
