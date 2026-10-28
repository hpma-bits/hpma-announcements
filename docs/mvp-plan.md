# MVP plan

Scope: [intent.md](intent.md). Five issues, in order. Each is independently
shippable and has a testable definition of done.

| # | Issue | Ships |
|---|---|---|
| MVP-1 | Minimal skeleton and data model | store, schema, CLI, tests |
| MVP-2 | Dashen corpus harvest | all posts' text + metadata, no images |
| MVP-3 | Selection study | LLM labels, induced procedure, evaluation |
| MVP-4 | Image archive | selected notices only |
| MVP-5 | Backfill and scheduled collection | full history, Actions, runbook |

## MVP-1: minimal skeleton and data model (done 2026-10-29)

**Background.** The repo had a five-channel framework, a generic CMS parser,
WebP derivatives, `raw/rev-N` history and a bilingual schema — with no data,
no tests, and a red CI. The overbuilt scaffold and obsolete channel docs were
removed on 2026-10-28.

**Shipped.** `Notice`/`Image` dataclasses, a deterministic store (`write`,
`iter_notices`, `write_index`, `validate`), `schema/notice.schema.json`, an
`index`/`validate` CLI, and store + CLI tests.

## MVP-2: Dashen corpus harvest

**Background.** The selection method must be derived from the full post
history, not guessed from a recent sample. The corpus is the evidence base for
every later decision, and it is committed to the repository.

**Goal.**

- `collectors/dashen.py`: paginated list walk, per-post parse, corpus writer.
- One JSON object per post in `corpus/dashen/posts.jsonl`: `id`, `type`,
  `published_at`, `source_updated_at`, `title`, `text`, `long_text`,
  `image_count`, `image_urls`, `topics`, `deleted`, `feed_url`.
- Title extraction: `body.title` → `body.subTitle` → first line of
  `body.text`.
- `corpus/dashen/manifest.json` records the run: `fetched_at`, page and post
  counts, API params.
- No image bytes are downloaded.
- Fixture-based tests: page parse, title fallbacks, pagination/dedup,
  byte-idempotent rewrite.

**Non-goals.** Image download, notice selection, notice records, OCR.

**Definition of done.**

- A full walk (~117 pages) produces one line per post, close to the account's
  `feedCount` (2,324); any gap is explained by deleted or excluded types.
- Rerunning the harvest is byte-identical and does not rewrite the manifest.
- `pytest -q` green.

## MVP-3: selection study

**Background.** "Official update announcement" is a product decision, not a
regex. The corpus makes it possible to derive and verify that decision instead
of asserting it.

**Taxonomy (decided 2026-10-29).**

- Positives: periodic update notices (`【M月D日更新公告】`), emergency
  maintenance notices, major version notices, version previews.
- Negatives: event/fashion/promo posts, community and birthday posts, videos,
  everything else.

**Goal.**

- Batch labeling: subagents label the corpus in chronological batches of
  40–60 posts; every label carries evidence, confidence and a reason. Labels,
  prompts and model/version are committed as a label ledger.
- Synthesis: one agent reads the batch results and produces the notice-series
  timeline, historical format variants, distinguishing features, hard
  positives/negatives, and an ordered decision procedure.
- Validation: implement the procedure as a deterministic classifier, run it
  over the corpus, compare against the labels; every false negative is
  reviewed, false positives are sampled; iterate until recall is 100% on the
  reviewed set.
- Golden-set tests: the reviewed notice list becomes the classifier's test
  fixture.
- Production posture: only the deterministic classifier runs in the scheduled
  job; plausible-but-unmatched posts go to a review queue.

**Non-goals.** Image download, notice records, runtime LLM calls.

**Definition of done.**

- The classifier reproduces the reviewed notice list with no false negatives
  and reviewed precision.
- Label ledger, prompts, evaluation report and decision procedure are
  committed.
- `pytest -q` green, including golden-set tests.

## MVP-4: image archive

**Background.** Notice bodies are only the maintenance window; the images are
the content. Images are downloaded only for notices selected by the MVP-3
procedure.

**Goal.**

- Download `body.media[]` in order; store under
  `assets/dashen/<feed_id>/<NN>.<ext>`.
- Record actual `sha256`, bytes, width, height; verify declared dimensions
  when present.
- Skip existing files with a matching hash; re-download on mismatch.
- `validate --check-assets` verifies existence and hash.
- Tests use a small local fixture image and a mocked HTTP fetch.

**Non-goals.** Re-encoding, thumbnails, OCR, CDN mirroring.

**Definition of done.** One real notice's images are committed, ordered and
open offline; a rerun is zero diff; validation passes.

## MVP-5: backfill and scheduled collection

**Background.** The pipeline is only worth anything once the history is in and
new notices arrive unattended.

**Goal.**

- Apply the classifier to the corpus and write notice records for the full
  history.
- Daily incremental run: first feed pages only, stop when a known id is
  reached; extend the corpus the same way.
- `collect.yml`: manual `backfill` input plus scheduled incremental; commit
  only on change; one concurrent run.
- Runbook: local run, full backfill, rerun, review queue triage, OCR later.
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
  Send the headers the web client uses: `GL-ClientType: 61`, `GL-DeviceId`,
  `Referer: https://ds.163.com/`.
- `result.nextRangeParam.maxTime` is the next-page cursor; stop when it
  repeats or no new ids appear.
- Detail: `GET /v1/web/feed/basic/facade?feedId=...`.
- `content` is a JSON string. `body.longText` exists only for type 6 articles.
- Title extraction chain: `body.title` → `body.subTitle` → first line of
  `body.text`.
- Corpus layout: `corpus/dashen/posts.jsonl` (one JSON object per line, sorted
  by `published_at`) plus `corpus/dashen/manifest.json`.
- Backfill ranges filter on `published_at` (post time), not `notice_date`.
- Do not drop records when `deleteTime` becomes non-zero; keep what has been
  archived.
