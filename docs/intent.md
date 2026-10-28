# Intent: HPMA official update-notice archive

Status: aligned 2026-10-28. This file is the single source of scope. The
multi-channel docs and scaffold were removed on 2026-10-28.

## One sentence

Archive every official **update notice** (更新公告) published by the verified
Harry Potter: Magic Awakened account on Dashen — body text plus every long
image — with provenance, in a public Git repository, and turn it into
searchable text for a future announcement-search site.

## Why

Game files contain configuration, cards and text, but not what the official
account announced, when, and in what words. Update notices live only on the
community platform: they get edited, deleted, or lost in redesigns. This
repository preserves that half of the information surface and, because it is a
public Git repository, stays citable and directly consumable.

It stays a separate repository because announcements are network content,
independent of the game client; Actions can commit directly with no
producer → data-repo handoff.

## Source (verified 2026-10-28)

- Single source: Dashen verified account `哈利波特魔法觉醒`, uid
  `29c10cd0344f462e82e16a516001f0dd` (2,324 posts).
- Public read-only GET API, no login, no XSRF signature:
  - list `GET https://inf.ds.163.com/v1/web/feed/basic/getSomeOneFeeds`
    (`someOneUid`, `maxTime`, `feedTypes=1,2,3,4,6,7,10,11`; 20 per page)
  - detail `GET /v1/web/feed/basic/facade?feedId=...`
  - headers used by the web client: `GL-ClientType: 61`, `GL-DeviceId`,
    `Referer: https://ds.163.com/`
- Notice filter: first line matches `【M月D日更新公告】`. Measured recent
  cadence is biweekly (Wednesday), 4–6 long images per notice.
- The body text carries only the maintenance window; the actual changes are in
  the images. Images are public on `ok.166.net`, watermarked.
- Canonical item URL: `https://ds.163.com/feed/<id>/`.

## MVP scope

1. Pull the account feed: full backfill (~117 pages), then incremental.
2. Keep only `【…更新公告】` items.
3. Write one JSON per notice with `id / url / published_at / notice_date /
   title / text / images[]`.
4. Download every image in order and commit the original bytes; record
   `sha256`, byte size and dimensions.
5. Write a date-descending `index.json`.
6. Run on a schedule in GitHub Actions; commit only on change.

### Non-goals (MVP)

Official website, YouTube, Weibo/NGA, in-game panel, event/fashion/activity
promos, cross-source dedupe, translation, bilingual pairing, per-revision raw
page archives, a generic channel framework, the search site itself.

## Data model

`data/dashen/<year>/<feed_id>.json`:

```json
{
  "id": "dashen.<feed_id>",
  "url": "https://ds.163.com/feed/<feed_id>/",
  "published_at": "2026-10-26T18:56:00+08:00",
  "source_updated_at": "2026-10-26T18:56:14+08:00",
  "notice_date": "2026-10-28",
  "title": "【10月28日更新公告】",
  "text": "...",
  "images": [
    {
      "index": 1,
      "url": "https://ok.166.net/...",
      "path": "assets/dashen/<feed_id>/01.jpg",
      "sha256": "...",
      "bytes": 296245,
      "width": 640,
      "height": 1234
    }
  ],
  "first_seen_at": "...",
  "revision": 1,
  "content_hash": "..."
}
```

- `text` is the native body and is never overwritten. OCR output goes to
  `derived/ocr/<id>.json`.
- `source_updated_at` detects official edits; MVP overwrites the record and
  increments `revision`, without keeping raw history.
- Images are stored as original bytes, not re-encoded (OCR needs fidelity).

## Storage policy (decision 1A)

- Images are committed to Git under `assets/dashen/<feed_id>/<NN>.<ext>`.
- Estimate: ~130 notices × 5 images × 300 KB ≈ 200 MB. Revisit LFS above
  500 MB.
- Re-running must be byte-idempotent.

## OCR (decision 2B)

- MVP stops at capture and archive. OCR is the next phase, not MVP.
- Before building the OCR pipeline, run a one-notice spike comparing PaddleOCR
  and Tesseract on a real notice; record engine, version and accuracy.
- OCR writes `derived/ocr/<id>.json` (engine, version, text, blocks,
  confidence) and never touches `text`.

## Acceptance (MVP)

- Every historical update notice is present: no missing dates, no duplicates.
- Every image is present, ordered, and opens offline.
- Re-running produces zero diff.
- A fresh clone can `collect` and `validate` without credentials.
- CI passes: tests, schema validation, image hash checks.

## Red lines

- Public read-only API only; low rate; this one official account only. No page
  scraping, no login bypass.
- `ds.163.com/robots.txt` disallows `/` for wildcard crawlers; the API host has
  none. Use the API only and state provenance in the README.
- Announcement text and images remain the rights holders' property. The repo is
  a research/preservation archive; takedown requests are honored.
- Repository-level metadata stays neutral; provenance lives in item records.
  See [ADR-0001](adr/0001-public-archive-content-policy.md).

## Phases

1. **MVP** (4 issues): skeleton + collector + images + backfill/automation.
2. **OCR**: one-notice spike, then full OCR and text export.
3. **Search site**: consumes the JSON + OCR text; the repo ships data, not UI.
