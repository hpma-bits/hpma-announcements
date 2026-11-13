# Dataset schema

`dataset/notices.jsonl` is the website-facing projection: one JSON object per
notice, one per line, sorted by `published_at` then `id`. It is derived data;
`python -m tools.cli dataset --check` fails when the committed file is not what
the inputs produce.

## Fields

| field | type | meaning |
|---|---|---|
| `id` | string | `<source>.<native_id>`, e.g. `dashen.6adf31ceee0069337849bde8` |
| `source` | string | `dashen` or `nga` |
| `canonical` | boolean | the preferred reading for this `(family, notice_date)`; exactly one row per dated group |
| `title` | string | the post/thread title |
| `notice_date` | string | date in the notice title (`YYYY-MM-DD`) |
| `published_at` | string | original post time, ISO 8601 with offset |
| `url` | string | canonical post URL |
| `source_author_uid` | string or null | NGA author uid; Dashen rows carry `author_uid` when present |
| `images` | string[] | repository-relative original image paths (empty for NGA) |
| `text` | string | readable text; see below |
| `text_sha256` | string | SHA-256 of the UTF-8 `text` |
| `sections` | object[] | per-image `{image, lines}` for Dashen rows; omitted when empty |
| `quality` | object | audit summary for Dashen rows; omitted for NGA |

### `text`

- **Dashen**: the image text. OCR blocks with the correction ledger applied
  (corrections, drops, inserts), the platform watermark removed, duplicate
  adjacent lines suppressed, lines in visual reading order. It is **not** the
  post's own body; the body is in the edition's `## 正文` and in
  `corpus/dashen/posts.jsonl`.
- **NGA**: the repost's own plain text, whitespace-normalised.

### `quality`

Dashen rows carry the audit summary read from `derived/audit/dashen/<id>.json`:

| field | meaning |
|---|---|
| `audit` | engine fingerprint of the second reading |
| `blocks` | audited units, excluding `queued` |
| `queued` | unreviewed blocks; must be 0 for `status: "verified"` |
| `consensus` / `corrected` / `adjudicated` | per-block status counts |
| `watermark_blocks` / `dropped_blocks` | non-text blocks removed from the edition |
| `inserted_lines` | printed lines the engine missed and the ledger inserts |
| `status` | `verified` when `queued == 0`, otherwise `in_progress` |

## Canonical

The preferred source is NGA (the operator's plain text); otherwise the earliest
Dashen row wins. Grouping is `(family, notice_date)`; a row without a
`notice_date` is never canonical. This mirrors `edition/notices.jsonl`; see
[CONTEXT.md](../CONTEXT.md) for the domain vocabulary.
