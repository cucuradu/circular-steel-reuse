"""Tests for the SteelReuse run-history manager (lib/steelreuse_runs.py).

Pure file/JSON management of saved match runs -- no Revit -- loaded by path like test_runner.py.
"""

import importlib.util
import json
import os

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_runs.py")
_spec = importlib.util.spec_from_file_location("steelreuse_runs", _LIB)
runs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runs)


def _results(tmp_path, tag):
    p = tmp_path / f"results_{tag}.json"
    p.write_text(json.dumps({"schema_version": 2, "kpis": {"reused": 1}}), encoding="utf-8")
    return str(p)


def _status(tmp_path, tag):
    p = tmp_path / f"status_{tag}.json"
    p.write_text(json.dumps({"donor": {"42": {"status": "reused"}}, "demand": {}, "summary": {}}),
                 encoding="utf-8")
    return str(p)


def test_record_archives_status_and_load_run_status_round_trips(tmp_path):
    hist = str(tmp_path / "h")
    runs.record_run(hist, "a", "", _results(tmp_path, "a"),
                    run_id="r1", status_path=_status(tmp_path, "a"))
    data = runs.load_run_status(hist, "r1")
    assert data is not None
    assert data["donor"]["42"]["status"] == "reused"
    # the archived status survives even after the original is gone (it was copied into the history)
    assert os.path.isfile(os.path.join(hist, runs.load_runs(hist)[0]["status_file"]))


def test_load_run_status_none_for_legacy_run_without_status(tmp_path):
    hist = str(tmp_path / "h")
    runs.record_run(hist, "a", "", _results(tmp_path, "a"), run_id="r1")  # no status_path
    assert runs.load_run_status(hist, "r1") is None


def test_delete_also_removes_archived_status(tmp_path):
    hist = str(tmp_path / "h")
    runs.record_run(hist, "a", "", _results(tmp_path, "a"),
                    run_id="r1", status_path=_status(tmp_path, "a"))
    status_file = os.path.join(hist, "status_r1.json")
    assert os.path.isfile(status_file)
    runs.delete_run(hist, "r1")
    assert not os.path.isfile(status_file)


def test_record_then_load_newest_first(tmp_path):
    hist = str(tmp_path / "steelreuse_runs")
    runs.record_run(hist, "baseline", "co2, cut", _results(tmp_path, "a"), run_id="20260616-100000")
    runs.record_run(hist, "no-cut", "co2, no-cut", _results(tmp_path, "b"), run_id="20260616-100100")
    loaded = runs.load_runs(hist)
    assert [r["name"] for r in loaded] == ["no-cut", "baseline"]   # newest first
    assert loaded[0]["params_label"] == "co2, no-cut"
    assert os.path.isfile(os.path.join(hist, loaded[0]["file"]))


def test_record_blank_name_defaults(tmp_path):
    hist = str(tmp_path / "h")
    e = runs.record_run(hist, "", "", _results(tmp_path, "a"), run_id="x1")
    assert e["name"] == "run"


def test_id_collision_gets_a_suffix(tmp_path):
    hist = str(tmp_path / "h")
    a = runs.record_run(hist, "a", "", _results(tmp_path, "a"), run_id="same")
    b = runs.record_run(hist, "b", "", _results(tmp_path, "b"), run_id="same")
    assert a["id"] != b["id"]
    assert len(runs.load_runs(hist)) == 2


def test_delete_removes_entry_and_file(tmp_path):
    hist = str(tmp_path / "h")
    e = runs.record_run(hist, "a", "", _results(tmp_path, "a"), run_id="r1")
    assert runs.delete_run(hist, "r1") is True
    assert runs.load_runs(hist) == []
    assert not os.path.isfile(os.path.join(hist, e["file"]))
    assert runs.delete_run(hist, "missing") is False


def test_load_run_data_round_trips(tmp_path):
    hist = str(tmp_path / "h")
    runs.record_run(hist, "a", "", _results(tmp_path, "a"), run_id="r1")
    data = runs.load_run_data(hist, "r1")
    assert data["schema_version"] == 2 and data["kpis"]["reused"] == 1


def test_load_runs_skips_missing_files(tmp_path):
    hist = str(tmp_path / "h")
    runs.record_run(hist, "a", "", _results(tmp_path, "a"), run_id="r1")
    os.remove(os.path.join(hist, "run_r1.json"))   # file gone, manifest entry remains
    assert runs.load_runs(hist) == []
