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


def test_occupancy_flags_build_zone_specs():
    from steelreuse.cli import _loads_from_args, build_parser

    args = build_parser().parse_args([
        "--donor", "d.json", "--demand", "m.json",
        "--occupancy", "residential-A", "--roof-occupancy", "roof-I",
        "--zone-override", "b7=balcony-A", "--no-load-reduction",
    ])
    loads = _loads_from_args(args)
    assert (loads.dead_kpa, loads.live_kpa) == (3.5, 1.5)            # residential-A (EN cat A floors)
    assert (loads.roof_dead_kpa, loads.roof_live_kpa) == (3.5, 3.0)  # roof-I
    assert loads.zone_overrides == {"b7": "balcony-A"}
    assert loads.load_reduction is False
    assert "balcony-A" in loads.custom_zones


def test_default_run_reproduces_office_floor_and_light_roof():
    from steelreuse.cli import _loads_from_args, build_parser

    args = build_parser().parse_args(["--donor", "d.json", "--demand", "m.json"])
    loads = _loads_from_args(args)
    assert (loads.dead_kpa, loads.live_kpa) == (3.5, 3.0)            # office-B == today
    assert (loads.roof_dead_kpa, loads.roof_live_kpa) == (1.0, 0.4)  # light roof-H
    assert loads.load_reduction is True


def test_national_annex_applies_to_occupancy():
    from steelreuse.cli import _loads_from_args, build_parser

    args = build_parser().parse_args(
        ["--donor", "d.json", "--demand", "m.json",
         "--national-annex", "it", "--occupancy", "residential-A"])
    loads = _loads_from_args(args)
    assert loads.live_kpa == 2.0          # Italy NTC residential q_k (EN base is 1.5)
    assert loads.roof_live_kpa == 0.5     # Italy coperture H1 (EN base roof-H 0.4)


def test_dead_live_override_occupancy_preset():
    from steelreuse.cli import _loads_from_args, build_parser

    args = build_parser().parse_args(
        ["--donor", "d.json", "--demand", "m.json", "--occupancy", "storage-E1", "--dead", "9"])
    loads = _loads_from_args(args)
    assert loads.dead_kpa == 9.0          # --dead wins
    assert loads.live_kpa == 7.5          # storage-E1 q_k still seeded


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


def test_results_out_records_sibling_artifact_paths(tmp_path):
    rep, status, results = (tmp_path / "r.html", tmp_path / "s.json", tmp_path / "res.json")
    rc = main(["--demo", "--out", str(rep), "--apply-matches-out", str(status),
               "--results-out", str(results)])
    assert rc == 0
    data = json.loads(results.read_text(encoding="utf-8"))
    assert data["paths"]["report"].endswith("r.html")
    assert data["paths"]["status"].endswith("s.json")
    assert data["paths"]["results"].endswith("res.json")


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
