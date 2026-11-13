"""Second-reader OCR for the cross-engine audit.

The v4 layer in ``derived/ocr/dashen/`` stays the engine of record for the
committed notices; this writes a parallel reading to
``derived/ocr-v6/dashen/`` so the audit can compare two independent readings of
the same image. The directory name is historical: it holds whichever engine is
the independent second reader, not necessarily v6.

* new notices use the v6 engine as the record, so their audit selects ``v4``;
* the committed v4 notices keep the default ``v6`` audit reading.

Nothing here touches the network: every image must already exist under
``assets/dashen/``.

    python -m tools.cli audit-ocr [--engine {v4,v6}] [--family update] [--limit N]

Idempotent: per-image results are cached by sha256 under the engine
fingerprint, so an interrupted run resumes without re-OCR.
"""

from __future__ import annotations

from pathlib import Path

from collectors import ocr

from . import archive

V6_DIR = "derived/ocr-v6/dashen"

Engine = ocr.Engine
Block = ocr.Block


def run(
    root: str | Path | None = None,
    *,
    family: str = "update",
    limit: int | None = None,
    engine: str = "v6",
) -> dict[str, int]:
    """OCR every notice image with the selected second-reader engine."""
    root_path = Path(root) if root else archive.REPO_ROOT
    engine_fn, engine_meta = ocr.build_engine(engine)
    return ocr.run(
        root_path,
        family=family,
        limit=limit,
        engine=engine_fn,
        engine_meta=engine_meta,
        out_dir=V6_DIR,
        download=False,
        delay=0,
    )
