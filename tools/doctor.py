"""Environment doctor for the announcement archive.

    python -m tools.cli doctor [--strict] [--require-ocr] [--require-audit] [--ocr-smoke]

Reports whether the running interpreter can drive the archive's tooling:

- the interpreter is the project virtual environment at ``<repo>/.venv``;
- the base dependencies (pytest, requests) import with usable versions;
- with ``--require-ocr``: the v4 engine of record
  (``rapidocr-onnxruntime``, PP-OCRv4) imports from inside the venv and its
  model files exist in the package;
- with ``--require-audit``: the v6 second reader (``rapidocr``, PP-OCRv6)
  imports from inside the venv and its model files exist in the package;
- with ``--ocr-smoke``: each installed engine reads one committed asset
  image fully offline and at least one must return text.

Statuses are ``ok``, ``warn`` or ``fail``. A ``warn`` never fails the run
unless ``--strict`` promotes it; any ``fail`` exits 1. Missing optional
engines are warnings unless the matching ``--require-*`` flag is given.
The report is human-readable lines plus one final JSON summary; everything
heavy (engines, models) is imported lazily so the doctor itself stays
dependency-light and CI-safe.

The pure helpers (path comparisons, model-file checks, version parsing,
candidate selection) live at module level so tests can exercise them with
fakes, without a venv, network or OCR engine.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_DEPS: dict[str, str] = {"pytest": ">=8", "requests": ">=2.32"}

# The v4 engine of record (collectors/ocr.py default_engine).
OCR_DIST = "rapidocr-onnxruntime"
OCR_MODULE = "rapidocr_onnxruntime"
OCR_MODELS = ("ch_PP-OCRv4_det_infer.onnx", "ch_PP-OCRv4_rec_infer.onnx")

# The v6 second reader.
AUDIT_DIST = "rapidocr"
AUDIT_MODULE = "rapidocr"
AUDIT_MODELS = ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx")

# Extensions collectors/ocr.py stores image bytes under (sniff_ext), minus
# the rare ".bin" fallback which is not a readable image anyway.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_doctor.py with fakes)


def norm_path(path: str | Path) -> Path:
    """Resolved path, case-normalised where the platform is case-insensitive."""
    return Path(os.path.normcase(os.path.abspath(str(path))))


def paths_equal(a: str | Path, b: str | Path) -> bool:
    return norm_path(a) == norm_path(b)


def inside_prefix(module_file: str | Path | None, prefix: str | Path) -> bool:
    """True when a module's ``__file__`` lives under ``prefix`` (the venv root)."""
    if not module_file:
        return False
    try:
        return norm_path(module_file).is_relative_to(norm_path(prefix))
    except ValueError:  # pragma: no cover - different drives on Windows
        return False


def module_problem(module_name: str, module_file: str | Path | None, prefix: str | Path) -> str | None:
    """Problem string when a module is missing or resolves outside ``prefix``."""
    if module_file is None:
        return f"{module_name} has no importable source file (namespace package?)"
    if not inside_prefix(module_file, prefix):
        return f"{module_name} resolves outside the virtual environment: {module_file}"
    return None


def venv_problem(sys_prefix: str | Path, base_prefix: str | Path, expected: str | Path) -> str | None:
    """Problem string when the interpreter is not the project venv at ``expected``.

    ``sys_prefix``/``base_prefix`` are ``sys.prefix``/``sys.base_prefix``;
    ``expected`` is normally ``<repo>/.venv``. Windows venv paths are compared
    case-insensitively; on POSIX the platform's own semantics apply.
    """
    if paths_equal(sys_prefix, base_prefix):
        return (
            "interpreter is not a virtual environment "
            "(run via .venv/Scripts/python.exe, .venv/bin/python, or `uv run`)"
        )
    if not paths_equal(sys_prefix, expected):
        return f"virtual environment is {sys_prefix}, expected {expected}"
    return None


def missing_model_files(package_dir: str | Path, filenames: tuple[str, ...] | list[str]) -> list[str]:
    """Model filenames that do not exist as regular files under ``package_dir``."""
    return [name for name in filenames if not (Path(package_dir) / name).is_file()]


def version_tuple(text: str) -> tuple[int, ...]:
    """Leading numeric components of a version string, e.g. '9.2.0rc1' -> (9, 2, 0)."""
    return tuple(int(part) for part in re.findall(r"\d+", text))


def satisfies_min(installed: str, minimum: str) -> bool:
    """Loose floor check: every numeric component of ``minimum`` is matched."""
    want = version_tuple(minimum)
    have = version_tuple(installed)[: len(want)]
    return have >= want


def apply_strict(checks: list[Check], strict: bool) -> list[Check]:
    """Promote warnings to failures when ``--strict`` is set."""
    if not strict:
        return checks
    return [
        Check(c.name, FAIL, c.detail + " (promoted by --strict)") if c.status == WARN else c
        for c in checks
    ]


def smoke_candidates(root: str | Path, limit: int = 5) -> list[Path]:
    """Committed asset images to try for the offline smoke test.

    Smallest first (byte size, then path) so the smoke run stays quick; some
    small images carry no text, so callers try candidates in order and stop at
    the first image an engine can read.
    """
    assets = Path(root) / "assets" / "dashen"
    if not assets.is_dir():
        return []
    images = [p for p in assets.glob("*/*") if p.suffix.lower() in IMAGE_EXTS]
    return sorted(images, key=lambda p: (p.stat().st_size, str(p)))[:limit]


def summarize(checks: list[Check]) -> dict:
    """Final JSON summary: aggregate status, exit code, per-check rows."""
    worst = FAIL if any(c.status == FAIL for c in checks) else WARN if any(c.status == WARN for c in checks) else OK
    return {
        "status": worst,
        "exit_code": 1 if worst == FAIL else 0,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
    }


# ---------------------------------------------------------------------------
# Probes (import things lazily; never called by the unit tests)


def _engine_check(name: str, dist: str, models: tuple[str, ...], prefix: Path, required: bool, extra: str) -> Check:
    """Check one engine: installed, imports from inside the venv, models on disk."""
    try:
        installed = version(dist)
    except PackageNotFoundError:
        hint = f"install with: uv sync --extra {extra}"
        return Check(name, FAIL if required else WARN, f"{dist} is not installed ({hint})")

    import importlib

    module_name = dist.replace("-", "_")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return Check(name, FAIL if required else WARN, f"{dist} {installed} is installed but does not import: {exc!r}")

    problem = module_problem(module_name, getattr(module, "__file__", None), prefix)
    if problem:
        return Check(name, FAIL if required else WARN, problem)

    package_dir = Path(module.__file__).parent / "models"
    missing = missing_model_files(package_dir, models)
    if missing:
        return Check(name, FAIL if required else WARN, f"model files missing under {package_dir}: {', '.join(missing)}")
    return Check(name, OK, f"{dist} {installed}, models under {package_dir}")


def _v4_blocks(image: str) -> tuple[str, int]:
    """Run the v4 engine the way collectors/ocr.py calls it; (error, blocks)."""
    try:
        from rapidocr_onnxruntime import RapidOCR

        result, _ = RapidOCR()(image, use_cls=False)
        return "", len(result or [])
    except Exception as exc:  # noqa: BLE001 - the smoke test reports, never crashes
        return repr(exc), 0


def _v6_blocks(image: str) -> tuple[str, int]:
    """Run the v6 engine; (error, blocks)."""
    try:
        from rapidocr import RapidOCR

        out = RapidOCR()(image)
        return "", len(getattr(out, "txts", None) or [])
    except Exception as exc:  # noqa: BLE001
        return repr(exc), 0


def _smoke_check(ocr_ok: bool, audit_ok: bool, required_any: bool) -> Check:
    """Run each installed engine on committed images, offline.

    Passes when at least one installed engine reads text from one of the
    smallest committed asset images. Never touches the network.
    """
    name = "ocr-smoke"
    engines: dict[str, object] = {}
    if ocr_ok:
        engines[OCR_MODULE] = _v4_blocks
    if audit_ok:
        engines[AUDIT_MODULE] = _v6_blocks
    if not engines:
        return Check(name, FAIL if required_any else WARN, "no OCR engine installed; smoke test skipped")

    candidates = smoke_candidates(REPO_ROOT)
    if not candidates:
        return Check(name, FAIL, "no committed asset images found under assets/dashen/")

    results: dict[str, str] = {engine: "" for engine in engines}
    blocks: dict[str, int] = {engine: 0 for engine in engines}
    tried: list[str] = []
    for image in candidates:
        if any(count > 0 for count in blocks.values()):
            break
        tried.append(str(image.relative_to(REPO_ROOT)))
        for engine, run in engines.items():
            if blocks[engine] > 0:
                continue
            error, count = run(str(image))
            blocks[engine] = max(blocks[engine], count)
            if error and not results[engine]:
                results[engine] = error

    parts = [f"{engine}: {count} blocks" for engine, count in blocks.items()]
    for engine, error in results.items():
        if error:
            parts.append(f"{engine} error: {error}")
    if any(count > 0 for count in blocks.values()):
        return Check(name, OK, f"image {tried[-1]} -> " + "; ".join(parts))
    return Check(name, FAIL, f"no engine read text from {len(tried)} committed images: " + "; ".join(parts))


# ---------------------------------------------------------------------------
# Entry point


def run(
    *,
    strict: bool = False,
    require_ocr: bool = False,
    require_audit: bool = False,
    ocr_smoke: bool = False,
    out=None,
) -> int:
    """Run every check, print the report plus JSON summary, return the exit code."""
    stream = out or sys.stdout
    prefix = Path(sys.prefix)
    checks: list[Check] = []

    # (a) interpreter is the project venv.
    problem = venv_problem(sys.prefix, sys.base_prefix, REPO_ROOT / ".venv")
    checks.append(
        Check("venv", FAIL if problem else OK, problem or f"{sys.prefix}")
    )

    # (b) base dependencies import with usable versions.
    for dist, minimum in BASE_DEPS.items():
        try:
            installed = version(dist)
        except PackageNotFoundError:
            checks.append(Check(f"base:{dist}", FAIL, f"{dist} is not installed"))
            continue
        if not satisfies_min(installed, minimum):
            checks.append(Check(f"base:{dist}", FAIL, f"{dist} {installed} is older than {minimum}"))
            continue
        try:
            __import__(dist)
        except ImportError as exc:
            checks.append(Check(f"base:{dist}", FAIL, f"{dist} {installed} does not import: {exc!r}"))
            continue
        checks.append(Check(f"base:{dist}", OK, f"{dist} {installed}"))

    # (c)/(d) the two OCR engines.
    ocr = _engine_check("engine:rapidocr_onnxruntime", OCR_DIST, OCR_MODELS, prefix, require_ocr, "ocr")
    audit = _engine_check("engine:rapidocr", AUDIT_DIST, AUDIT_MODELS, prefix, require_audit, "audit")
    checks.append(ocr)
    checks.append(audit)

    # (e) offline smoke test against a committed image.
    if ocr_smoke:
        ocr_ok = ocr.status == OK
        audit_ok = audit.status == OK
        checks.append(
            _smoke_check(ocr_ok, audit_ok, required_any=require_ocr or require_audit)
        )

    checks = apply_strict(checks, strict)
    summary = summarize(checks)

    for check in checks:
        marker = {"ok": "ok", "warn": "WARN", "fail": "FAIL"}[check.status]
        print(f"{marker:4} {check.name}: {check.detail}", file=stream)
    print(json.dumps(summary, ensure_ascii=False), file=stream)
    return summary["exit_code"]
