# Audit evidence

Primary data for the cross-engine audit. These files are the human decisions
behind `derived/audit/dashen/`; the per-block ledger there is a projection that
`python -m tools.cli audit-status` rebuilds from this directory. Losing either
group means the audit record can no longer be reproduced, so both are tracked.

```
audit/verdicts-*.jsonl    adjudicated verdicts (3,300 rows, 17 files)
audit/adj-*.jsonl         the review queues the verdicts answer (6,108 rows, 17 files)
```

The source-image crops referenced by `crop` are *not* tracked: they are
regenerable scratch and stay in `.review/audit/` with the rest of the review
workspace (`.review/` is gitignored). `audit-status` never reads them.

## `verdicts-*.jsonl`

One adjudicated audit item per line, written after a reviewer inspected the
crop (or applied a ratified punctuation rule for sampled batches):

| field         | meaning                                                        |
|---------------|----------------------------------------------------------------|
| `notice`      | Dashen feed id (native id, no `dashen.` prefix)                |
| `image_index` | 1-based image index within the notice                          |
| `v4_block`    | 0-based block index in the v4 record, or `null` for v6-only    |
| `v6_block`    | 0-based block index in the v6 reading, or `null` for v4-only   |
| `kind`        | queue kind: `differ` / `punct` / `v6_only` / `v4_only` / `table_row` / `lowconf` / `kept_twin` |
| `verdict`     | decision; see below                                            |
| `corrected`   | replacement text, or `null` when the verdict needs none        |
| `confidence`  | reviewer confidence (`high` / `medium` / `low`)                |
| `reason`      | short adjudication note, often naming the crop                 |
| `crop`        | untracked evidence path under `.review/audit/`                 |

Verdict vocabulary: `correct` / `v6_right` (accept the v6 text into the
ledger), `drop` / `v6_garbage` (remove the block), `v4_right` / `v4_ok` /
`record` (keep the v4 record), `v4_covered` / `cover_art` (the content is
already carried elsewhere), `source_issue` (printed that way; keep it),
`keep` / `confirm` / `modify` / `revert`, and `missed_line` (v6 found a
printed line v4 lacks). The rebuild only consumes the locator fields and
`verdict`; the rest is the audit trail.

## `adj-*.jsonl`

The review queues produced by `audit-compare`: every v4/v6 disagreement as it
was handed to a reviewer, with `kind`, both raw texts (`v4_text`, `v6_text`)
and an optional `crop` path. These rows carry no verdict.

The rebuild needs them as a *memory of what was queued*: a v6 block that was
paired with a v4 block at queue time can become `v6_only` after later ledger
drops. A locator that appeared in any queue was adjudicated together with its
pair and must not be re-queued by the recomparison. Rebuilding from
`verdicts-*.jsonl` alone leaves 10 such blocks queued.

Several queues exist both whole and split into `-pN` parts; the duplicate
rows are harmless because the rebuild reduces them to locator maps/sets.

## Rebuilding and checking

```bash
# rewrite derived/audit/dashen/ from this directory
uv run python -m tools.cli audit-status

# verify the committed projection byte for byte, writing nothing
uv run python -m tools.cli audit-status --check
```

Both modes read only `audit/`. If either `verdicts-*.jsonl` or `adj-*.jsonl`
is absent, the command fails before touching `derived/audit/`; `--check` also
fails when a committed ledger is missing, stale, or orphaned, and when any
block remains `queued`. Writes go through a temporary file and `os.replace`,
so an interrupted run never leaves a half-written ledger.
