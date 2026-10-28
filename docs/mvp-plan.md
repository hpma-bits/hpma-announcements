# MVP plan

Scope: [intent.md](intent.md). Four issues, in order. Each is independently
shippable and has a testable definition of done.

| # | Issue | Ships |
|---|---|---|
| MVP-1 | Minimal skeleton and data model | store, schema, CLI, tests |
| MVP-2 | Dashen collector | list, filter, parse |
| MVP-3 | Image archive | download, hash, validate |
| MVP-4 | Backfill and scheduled collection | full history, Actions, runbook |

## MVP-1: minimal skeleton and data model

**Background.** The repo had a five-channel framework, a generic CMS parser,
WebP derivatives, `raw/rev-N` history and a bilingual schema — with no data,
no tests, and a red CI. The overbuilt scaffold and obsolete channel docs were
removed on 2026-10-28. What remains is the minimal skeleton.

**Goal.**

- Add a minimal store: one `Notice` dataclass, `write()` with `first_seen_at` /
  `revision` / `content_hash`, `iter_notices()`, `write_index()`.
- Add `schema/notice.schema.json`.
- CLI: `collect --source dashen`, `index`, `validate`.
- Add `tests/` covering write idempotency and schema validation.

**Non-goals.** Network access, image download, OCR.

**Definition of done.**

- `pytest -q` passes with at least the store tests.
- `python -m collectors.cli validate` passes on an empty `data/`.
- CI green.

## MVP-2: Dashen collector

**Background.** The source API is verified; the collector is the core of the
MVP.

**Goal.**

- Add `collectors/dashen.py` with list pagination, notice filter and detail
  parse.
- Filter: first line matches `^【\d{1,2}月\d{1,2}日更新公告】`.
- Parse the `content` JSON string: `body.text`, `body.media[]`.
- Derive `notice_date` from the title; infer the year from `published_at`
  (choose the candidate date within ±180 days of the post).
- Source URL `https://ds.163.com/feed/<id>/`.
- Store image metadata (URL, order, declared size/dimensions) but do not
  download bytes yet.
- Fixtures: one list page and one notice content JSON.
- Tests: the filter matches the 9 known notices in the 120-item sample; one
  notice parses; a rerun is unchanged.

**Non-goals.** Image bytes, OCR, deleted-post recovery.

**Definition of done.** Backfilling 2026-07-01..2026-10-28 writes exactly the
9 known notices; a rerun produces zero diff.

## MVP-3: image archive

**Background.** Notice bodies are only the maintenance window; the images are
the content.

**Goal.**

- Download `body.media[]` in order; store under
  `assets/dashen/<feed_id>/<NN>.<ext>`.
- Record actual `sha256`, bytes, width, height; verify declared dimensions when
  present.
- Skip existing files with a matching hash; re-download on mismatch.
- `validate --check-assets` verifies existence and hash.
- Tests use a small local fixture image and a mocked HTTP fetch.

**Non-goals.** Re-encoding, thumbnails, OCR, CDN mirroring.

**Definition of done.** One real notice's images are committed, ordered and
open offline; a rerun is zero diff; validation passes.

## MVP-4: historical backfill and scheduled collection

**Background.** The pipeline is only worth anything once the history is in and
new notices arrive unattended.

**Goal.**

- Walk the full feed (2,324 posts, ~117 pages) once; keep all notices.
- Daily incremental run: first page only, stop when a known id is reached.
- `collect.yml`: manual `backfill` input plus scheduled incremental; commit
  only on change; one concurrent run.
- Runbook: local run, full backfill, rerun, OCR later.
- README: source, provenance/license note, how to consume the JSON.

**Non-goals.** Deleted-post detection, revision UI, search site.

**Definition of done.** All historical notices are present and date-continuous;
the scheduled workflow is green; a fresh clone reproduces the archive without
credentials.

## Next phase (not MVP): OCR spike

- Pick the latest notice; run PaddleOCR and Tesseract on its images.
- Produce `derived/ocr/<id>.json`; measure character accuracy on a hand-read
  sample.
- Pick the engine, record version and settings, then decide whether to
  backfill OCR for all notices.

## Technical notes

- List:
  `GET https://inf.ds.163.com/v1/web/feed/basic/getSomeOneFeeds?someOneUid=<uid>&feedTypes=1,2,3,4,6,7,10,11&maxTime=<ms>`.
  Required headers: `GL-ClientType: 61`, `GL-DeviceId`,
  `Referer: https://ds.163.com/`.
- `result.nextRangeParam.maxTime` is the next-page cursor; stop when it repeats
  or no new ids appear.
- Detail: `GET /v1/web/feed/basic/facade?feedId=...`.
- `content` is a JSON string. `body.longText` exists only for type 6 articles,
  not for the target notices, so do not rely on it.
- Backfill ranges filter on `published_at` (post time), not `notice_date`.
- Sample counts: 20 items per page; 9 notices in the 2026-07-06..2026-10-28
  sample.
- Do not drop records when `deleteTime` becomes non-zero; keep what has been
  archived.
