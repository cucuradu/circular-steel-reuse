"""CLI smoke tests: --version, the no-input error, bundled samples, and graceful failures."""

import json

import pytest

from steelreuse import __version__
from steelreuse.cli import main
from steelreuse.resources import sample_path
from steelreuse.schema import ExtractedModel, ExtractionError


def test_version_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_inputs_errors_with_exit_two():
    # argparse uses exit code 2 for usage errors.
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_bundled_samples_resolve():
    assert sample_path("donor.json").exists()
    assert sample_path("demand.json").exists()


def test_missing_file_exits_one_with_message(tmp_path, capsys):
    missing = str(tmp_path / "nope.json")
    assert main(["--donor", missing, "--demand", missing]) == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_malformed_json_exits_one_no_traceback(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    assert main(["--donor", str(bad), "--demand", str(bad)]) == 1
    assert "error" in capsys.readouterr().err.lower()


def test_results_out_writes_versioned_contract(tmp_path):
    rp = tmp_path / "results.json"
    rc = main([
        "--donor", str(sample_path("donor.json")),
        "--demand", str(sample_path("demand.json")),
        "--out", str(tmp_path / "report.html"),
        "--results-out", str(rp),
    ])
    assert rc == 0
    assert rp.exists()
    data = json.loads(rp.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert "assignments" in data and "kpis" in data


def test_load_rejects_non_object_top_level(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("[]", encoding="utf-8")  # valid JSON, but not an object
    with pytest.raises(ExtractionError):
        ExtractedModel.load(f)


def test_load_rejects_non_numeric_length(tmp_path):
    f = tmp_path / "y.json"
    f.write_text('{"kind": "donor", "members": [{"id": "b1", "length_mm": "oops"}]}', encoding="utf-8")
    with pytest.raises(ExtractionError):
        ExtractedModel.load(f)
