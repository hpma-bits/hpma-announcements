# ADR-0001: Public archive content policy

- Status: accepted
- Date: 2026-10-28

## Context

This repository is public and archives official game announcements, including
their text and images. That is different from the extraction-derived datasets in
the producer repository, whose public artifacts follow a neutral-wording rule
(no source/lineage blocks, no internal table paths, no producer script names).

An announcement archive cannot be neutralized: the announcement text itself is
the content, and its source URL/date are essential provenance. Applying the
neutral-wording rule to the archived content would make the archive useless.

## Decision

1. This repository is a **public archive**. Item records include `url`,
   `channel`, `published_at` and `source_meta` as essential provenance.
2. The neutral-wording rule applies to **repository-level metadata** (README,
   release titles, repository description, issue templates), not to archived
   content or its provenance fields.
3. Repository-level metadata must not describe the producer pipeline, internal
   table/path names, or generation scripts.
4. Copyright remains with the rights holders. The repository is an unofficial
   research/preservation archive; takedown requests are honored.
5. v1 does not bypass login walls. Auth-gated channels are manual by default; a
   cookie/token may be added later only with explicit opt-in and never committed.

## Consequences

- The archive remains useful and verifiable: every item points back to its
  public source.
- The public repository can be indexed and quoted, but it does not claim
  ownership of the content.
- If a rights holder requests removal, the affected records/assets are removed
  from the current tree and the request is documented in the issue tracker.
