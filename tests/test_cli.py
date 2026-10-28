import json

from collectors.cli import main


def test_validate_empty_root(tmp_path, capsys):
    assert main(["validate", "--root", str(tmp_path)]) == 0
    assert "validation ok" in capsys.readouterr().out


def test_index_empty_root(tmp_path):
    assert main(["index", "--root", str(tmp_path)]) == 0
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index == {"count": 0, "items": []}


def test_validate_fails_on_bad_record(tmp_path, capsys):
    path = tmp_path / "data" / "dashen" / "2026" / "bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")

    assert main(["validate", "--root", str(tmp_path)]) == 1
    assert "bad.json" in capsys.readouterr().err
