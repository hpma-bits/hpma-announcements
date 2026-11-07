"""Unit tests for tools/doctor.py's pure helpers.

These run without a virtual environment, OCR engine or network: every helper
under test takes plain paths, strings or fakes. CI has none of the optional
machinery, so nothing here may import it.
"""

import sys

from tools import doctor


# ---------------------------------------------------------------------------
# Path comparison


def test_paths_equal_is_exact_on_identical_paths(tmp_path):
    assert doctor.paths_equal(tmp_path, tmp_path)
    assert doctor.paths_equal(str(tmp_path), str(tmp_path) + "/")


def test_paths_equal_windows_case_insensitive():
    if sys.platform != "win32":
        return  # POSIX paths are case-sensitive by design.
    assert doctor.paths_equal(r"E:\Repo\.venv", r"e:\repo\.VENV")


def test_paths_equal_distinguishes_siblings(tmp_path):
    (a := tmp_path / "a").mkdir()
    (b := tmp_path / "b").mkdir()
    assert not doctor.paths_equal(a, b)


# ---------------------------------------------------------------------------
# venv check


def test_venv_problem_when_not_a_venv(tmp_path):
    # sys.prefix == sys.base_prefix means a bare interpreter.
    problem = doctor.venv_problem(tmp_path, tmp_path, tmp_path / ".venv")
    assert problem is not None
    assert "not a virtual environment" in problem


def test_venv_problem_when_wrong_location(tmp_path):
    (venv := tmp_path / "elsewhere").mkdir()
    problem = doctor.venv_problem(venv, tmp_path / "base", tmp_path / ".venv")
    assert problem is not None
    assert "expected" in problem


def test_venv_ok_at_expected_location(tmp_path):
    (venv := tmp_path / ".venv").mkdir()
    assert doctor.venv_problem(venv, tmp_path / "base", tmp_path / ".venv") is None


def test_venv_ok_with_windows_case_difference(tmp_path):
    if sys.platform != "win32":
        return
    (venv := tmp_path / ".venv").mkdir()
    assert doctor.venv_problem(venv, tmp_path / "base", tmp_path / ".VENV") is None


# ---------------------------------------------------------------------------
# package location check


def test_module_problem_when_file_is_none():
    problem = doctor.module_problem("rapidocr", None, "/any/prefix")
    assert problem is not None
    assert "no importable source file" in problem


def test_module_problem_when_outside_prefix(tmp_path):
    (prefix := tmp_path / "venv").mkdir()
    (outside := tmp_path / "global" / "site-packages" / "rapidocr.py").parent.mkdir(parents=True)
    problem = doctor.module_problem("rapidocr", outside, prefix)
    assert problem is not None
    assert "outside" in problem


def test_module_problem_none_when_inside_prefix(tmp_path):
    (prefix := tmp_path / "venv").mkdir()
    (module := prefix / "lib" / "site-packages" / "rapidocr.py").parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    assert doctor.module_problem("rapidocr", module, prefix) is None


def test_inside_prefix_nested_and_sibling(tmp_path):
    prefix = tmp_path / "venv"
    deep = prefix / "lib" / "site-packages" / "pkg" / "__init__.py"
    assert doctor.inside_prefix(deep, prefix)
    assert not doctor.inside_prefix(tmp_path / "other.py", prefix)
    assert not doctor.inside_prefix(None, prefix)


def test_inside_prefix_windows_case_insensitive():
    if sys.platform != "win32":
        return
    assert doctor.inside_prefix(r"E:\Repo\.venv\Lib\site-packages\x.py", r"e:\repo\.VENV")


# ---------------------------------------------------------------------------
# model files


def test_missing_model_files_reports_only_missing(tmp_path):
    (models := tmp_path / "models").mkdir()
    (models / "det.onnx").write_bytes(b"x")
    missing = doctor.missing_model_files(models, ("det.onnx", "rec.onnx"))
    assert missing == ["rec.onnx"]


def test_missing_model_files_all_present(tmp_path):
    (models := tmp_path / "models").mkdir()
    for name in ("det.onnx", "rec.onnx"):
        (models / name).write_bytes(b"x")
    assert doctor.missing_model_files(models, ("det.onnx", "rec.onnx")) == []


def test_missing_model_files_rejects_directories(tmp_path):
    (models := tmp_path / "models" / "det.onnx").mkdir(parents=True)
    assert doctor.missing_model_files(models.parent, ("det.onnx",)) == ["det.onnx"]


# ---------------------------------------------------------------------------
# version parsing


def test_version_tuple_numeric_prefix():
    assert doctor.version_tuple("9.2.0") == (9, 2, 0)
    assert doctor.version_tuple("1.31.1") == (1, 31, 1)
    assert doctor.version_tuple("2.32.3") == (2, 32, 3)


def test_satisfies_min():
    assert doctor.satisfies_min("9.2.0", ">=8")
    assert doctor.satisfies_min("2.32.3", ">=2.32")
    assert not doctor.satisfies_min("2.31.9", ">=2.32")
    assert doctor.satisfies_min("8.0.0", ">=8")


# ---------------------------------------------------------------------------
# strict promotion and summary


def test_apply_strict_promotes_warnings_only():
    checks = [
        doctor.Check("a", doctor.OK, "fine"),
        doctor.Check("b", doctor.WARN, "meh"),
        doctor.Check("c", doctor.FAIL, "bad"),
    ]
    assert [c.status for c in doctor.apply_strict(checks, strict=False)] == [
        doctor.OK,
        doctor.WARN,
        doctor.FAIL,
    ]
    promoted = doctor.apply_strict(checks, strict=True)
    assert [c.status for c in promoted] == [doctor.OK, doctor.FAIL, doctor.FAIL]
    assert "--strict" in promoted[1].detail


def test_summarize_exit_codes():
    ok = doctor.summarize([doctor.Check("a", doctor.OK, "")])
    warn = doctor.summarize([doctor.Check("a", doctor.WARN, "")])
    fail = doctor.summarize([doctor.Check("a", doctor.FAIL, "")])
    assert (ok["status"], ok["exit_code"]) == (doctor.OK, 0)
    assert (warn["status"], warn["exit_code"]) == (doctor.WARN, 0)
    assert (fail["status"], fail["exit_code"]) == (doctor.FAIL, 1)
    assert fail["checks"][0]["name"] == "a"


# ---------------------------------------------------------------------------
# smoke candidate selection


def test_smoke_candidates_smallest_first(tmp_path):
    (assets := tmp_path / "assets" / "dashen" / "feed1").mkdir(parents=True)
    big = assets / "01.png"
    small = assets / "02.jpg"
    big.write_bytes(b"x" * 100)
    small.write_bytes(b"x" * 10)
    assert doctor.smoke_candidates(tmp_path) == [small, big]


def test_smoke_candidates_ignores_non_images_and_ties(tmp_path):
    (assets := tmp_path / "assets" / "dashen" / "feed1").mkdir(parents=True)
    (assets / "01.txt").write_bytes(b"x")
    (assets / "02.jpg").write_bytes(b"y")
    (assets / "nested").mkdir()
    (assets / "nested" / "03.png").write_bytes(b"z")  # too deep for */*
    candidates = doctor.smoke_candidates(tmp_path)
    assert [p.name for p in candidates] == ["02.jpg"]


def test_smoke_candidates_limit(tmp_path):
    (assets := tmp_path / "assets" / "dashen").mkdir(parents=True)
    for index in range(8):
        (assets / f"f{index}").mkdir()
        (assets / f"f{index}" / "01.png").write_bytes(b"x" * (index + 1))
    assert len(doctor.smoke_candidates(tmp_path, limit=5)) == 5
    assert len(doctor.smoke_candidates(tmp_path)) == 5


def test_smoke_candidates_empty_when_no_assets(tmp_path):
    assert doctor.smoke_candidates(tmp_path) == []
