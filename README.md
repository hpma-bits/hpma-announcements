# hpma-announcements

Every update notice (更新公告) published by the verified *Harry Potter: Magic
Awakened* account `哈利波特魔法觉醒` on Dashen, archived as data and as readable
text: the post itself, its long images as original bytes, OCR of those images,
the human corrections on top of that OCR, and a Markdown edition.

This is an unofficial research and preservation archive. Announcement text and
images remain the property of their rights holders; takedown requests are
honoured. Notices live only on the community platform, where they get edited,
deleted, or lost in redesigns, and the game files do not contain them.

## What is in the archive

```
corpus/dashen/posts.jsonl                   2,324 posts: everything the account published
study/labels.jsonl                          which posts are notices, and their notice date
assets/dashen/<feed_id>/<NN>.<ext>          original image bytes (650 images)
derived/ocr/dashen/<feed_id>.json           OCR engine output, immutable
derived/ocr/corrections/dashen/<id>.json    the correction ledger: human decisions
edition/dashen/<feed_id>.md                 the readable edition, one file per notice
edition/notices.jsonl                       one line per notice, metadata only
```

162 of the 2,324 posts are update notices. Only those have images, OCR and an
edition; the rest (events, fashion, videos, community posts) are kept in the
corpus because the selection was derived from it.

`study/` holds the offline evidence for that selection: the labelling prompt,
the evaluation report and the selected ids.

## Reading the edition

Each notice is one Markdown file with YAML front matter and two optional
sections:

```markdown
---
id: "dashen.6adf31ceee0069337849bde8"
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

`edition/notices.jsonl` repeats the metadata, one JSON object per notice, oldest
first, for consumers that do not want to parse Markdown.

## Rebuilding

```bash
pip install -r requirements.txt
python -m collectors.cli corpus     # refresh posts from the read-only Dashen API
python -m collectors.cli ocr        # download images and OCR them (needs requirements-ocr.txt)
python -m tools.cli verify          # check every ledger entry against the raw OCR
python -m tools.cli edition         # rewrite edition/ from the data
```

`python -m tools.cli edition --check` exits non-zero when the committed edition
is not what the data produces. A fresh clone needs neither network access nor an
OCR engine for the tests, `verify` or `edition --check`.

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
- 819 corrections cover 153 of the 162 notices; the block text is the only thing
  the ledger changes, `derived/ocr/` is never edited.

## Checking

```bash
pytest
python -m tools.cli verify
python -m tools.cli edition --check
```

CI runs the same three commands on a Linux runner without network, OCR engine or
Pillow.

## Known gaps

- 16 confusable words are not yet checked glyph by glyph against the images:
  石墩/石礅, 金锻/金缎, 银辉之匙/银辉之钥, 子立/孑立, 戈德利克/戈德里克,
  鲁本·海格/鲁伯·海格, 长跑专卖店/长袍专卖店, 云雾飘渺/云雾缥缈,
  凤吟凰鸣/凰吟凤鸣, 讽爽英姿/飒爽英姿, 进发出/迸发出, 换新款/焕新款,
  等级上线/等级上限, 补获/捕获, 无暇信念/无瑕信念, 登陆/登录.
- The edition covers the update family only. Maintenance, version and
  test-server notices are labelled in `study/labels.jsonl` but have no images,
  OCR or edition yet.

## Source

- Dashen verified account `哈利波特魔法觉醒`, uid `29c10cd0344f462e82e16a516001f0dd`.
- Public read-only GET API, no login and no request signing:
  - list `https://inf.ds.163.com/v1/web/feed/basic/getSomeOneFeeds`
  - detail `https://inf.ds.163.com/v1/web/feed/basic/facade`
  - headers the web client sends: `GL-ClientType: 61`, `GL-DeviceId`,
    `Referer: https://ds.163.com/`
- Canonical item URL: `https://ds.163.com/feed/<id>/` (type-6 articles use
  `/article/<id>/`).
- Only this account is fetched, at a low rate, from the JSON API. No page
  scraping, no login bypass; `ds.163.com/robots.txt` disallows `/` for wildcard
  crawlers, so the API host is used instead.
