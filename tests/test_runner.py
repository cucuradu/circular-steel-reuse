"""Tests for the pyRevit extension's orchestration core (lib/steelreuse_runner.py).

Only the *pure* logic is tested here -- mapping the Run Match form's options to a CLI argument list
and discovering the signed-venv interpreter. The subprocess launch + background-thread execution are
Revit-side (.NET threading) and verified manually inside Revit, not here.

The module is IronPython-safe (stdlib only) so it also imports cleanly under CPython for these tests.
"""

import importlib.util
import json
import os
import sys

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_runner.py")
_spec = importlib.util.spec_from_file_location("steelreuse_runner", _LIB)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _pair(cmd, flag):
    """The value argparse would bind to ``flag`` (the token right after it)."""
    return cmd[cmd.index(flag) + 1]


def test_command_starts_with_wdac_safe_module_invocation():
    cmd = runner.build_command("C:/venv/python.exe", {"donor": "d.json", "demand": "m.json"}, "C:/out")
    # Must run the module, never the WDAC-blocked steelreuse.exe console launcher.
    assert cmd[:3] == ["C:/venv/python.exe", "-m", "steelreuse.cli"]
    assert "steelreuse.exe" not in " ".join(cmd)


def test_command_always_emits_the_three_artifacts_under_out_dir():
    cmd = runner.build_command("py", {"donor": "d.json", "demand": "m.json"}, os.path.join("C:", "out"))
    assert _pair(cmd, "--donor") == "d.json"
    assert _pair(cmd, "--demand") == "m.json"
    # status.json (Apply Matches), report.html, results.json (the panel) -- all under out_dir.
    assert _pair(cmd, "--apply-matches-out").endswith("status.json")
    assert _pair(cmd, "--out").endswith("report.html")
    assert _pair(cmd, "--results-out").endswith("results.json")
    for flag in ("--apply-matches-out", "--out", "--results-out"):
        assert os.path.join("C:", "out") in _pair(cmd, flag)


def test_command_defaults_objective_co2_and_cutting_on():
    cmd = runner.build_command("py", {"donor": "d", "demand": "m"}, "o")
    assert _pair(cmd, "--objective") == "co2"
    assert "--no-cut" not in cmd          # cutting-stock is the default
    assert "--frame-analysis" not in cmd  # analysis off unless asked


def test_command_toggles_conditional_flags():
    opts = {"donor": "d", "demand": "m", "objective": "members", "cut": False,
            "frame_analysis": True, "phi": 0.005, "min_util": 0.3, "verify_match": True}
    cmd = runner.build_command("py", opts, "o")
    assert _pair(cmd, "--objective") == "members"
    assert "--no-cut" in cmd
    assert "--frame-analysis" in cmd
    assert _pair(cmd, "--phi") == "0.005"
    assert _pair(cmd, "--min-util") == "0.3"
    assert "--verify-match" in cmd


def test_command_forwards_occupancy_and_load_reduction():
    opts = {"donor": "d", "demand": "m", "occupancy": "residential-A",
            "roof_occupancy": "roof-I", "load_reduction": False}
    cmd = runner.build_command("py", opts, "o")
    assert _pair(cmd, "--occupancy") == "residential-A"
    assert _pair(cmd, "--roof-occupancy") == "roof-I"
    assert "--no-load-reduction" in cmd


def test_command_forwards_national_annex_only_when_not_en():
    on = runner.build_command("py", {"donor": "d", "demand": "m", "national_annex": "it"}, "o")
    assert _pair(on, "--national-annex") == "it"
    off = runner.build_command("py", {"donor": "d", "demand": "m", "national_annex": "en"}, "o")
    assert "--national-annex" not in off


def test_command_omits_load_reduction_flag_when_on():
    # reduction is ON by default -> the disable flag must NOT be emitted
    cmd = runner.build_command("py", {"donor": "d", "demand": "m", "load_reduction": True}, "o")
    assert "--no-load-reduction" not in cmd


def test_command_omits_zero_valued_optional_flags():
    opts = {"donor": "d", "demand": "m", "phi": 0.0, "wind": 0.0, "seismic": 0.0, "min_util": 0.0}
    cmd = runner.build_command("py", opts, "o")
    for flag in ("--phi", "--wind", "--seismic", "--min-util"):
        assert flag not in cmd


def test_command_emits_carbon_and_policy_flags():
    opts = {"donor": "d", "demand": "m", "counterfactual": "recycling", "pareto": True,
            "disposition": True, "w_overspec": 1.5, "reserve": 0.4, "connections": True,
            "moment_shape": True, "max_distinct_sections": 8}
    cmd = runner.build_command("py", opts, "o")
    assert _pair(cmd, "--counterfactual") == "recycling"
    assert "--pareto" in cmd and "--disposition" in cmd and "--connections" in cmd
    assert "--moment-shape" in cmd
    assert _pair(cmd, "--w-overspec") == "1.5"
    assert _pair(cmd, "--reserve") == "0.4"
    assert _pair(cmd, "--max-distinct-sections") == "8"


def test_command_emits_load_and_frame_flags():
    opts = {"donor": "d", "demand": "m", "dead": 4.0, "live": 2.5, "gamma_g": 1.35, "gamma_q": 1.5,
            "trib_width": 2.5, "col_trib_area": 12.0, "col_floors": 3, "col_ecc": 50,
            "construction": True, "construction_live": 0.9, "wind_uplift": 1.2,
            "solver": "sap2000", "pdelta": True, "all_demand": True, "knockdown": 0.9}
    cmd = runner.build_command("py", opts, "o")
    assert _pair(cmd, "--dead") == "4.0" and _pair(cmd, "--live") == "2.5"
    assert _pair(cmd, "--col-floors") == "3" and _pair(cmd, "--col-ecc") == "50"
    assert "--construction" in cmd and _pair(cmd, "--construction-live") == "0.9"
    assert _pair(cmd, "--wind-uplift") == "1.2"
    assert _pair(cmd, "--solver") == "sap2000" and "--pdelta" in cmd
    assert "--all-demand" in cmd and _pair(cmd, "--knockdown") == "0.9"


def test_command_supports_several_demand_models_for_portfolio():
    cmd = runner.build_command("py", {"donor": "d", "demand": ["a.json", "b.json"]}, "o")
    i = cmd.index("--demand")
    assert cmd[i + 1] == "a.json" and cmd[i + 2] == "b.json"


def test_candidate_interpreters_finds_a_venv_up_the_tree(tmp_path):
    # <root>/.venv-signed/Scripts/python.exe, started from a few levels below (like the extension dir)
    scripts = tmp_path / ".venv-signed" / "Scripts"
    scripts.mkdir(parents=True)
    py = scripts / "python.exe"
    py.write_text("", encoding="utf-8")
    start = tmp_path / "circular-steel-reuse" / "pyrevit_extension" / "SteelReuse.extension"
    start.mkdir(parents=True)
    cands = runner.candidate_interpreters(str(start))
    assert str(py) in cands


def test_candidate_interpreters_empty_when_no_venv(tmp_path):
    start = tmp_path / "nothing-here"
    start.mkdir()
    assert runner.candidate_interpreters(str(start)) == []


def test_verify_interpreter_true_for_a_python_with_steelreuse():
    # this interpreter (the signed venv pytest runs under) can import steelreuse -> verifies
    assert runner.verify_interpreter(sys.executable) is True


def test_verify_interpreter_false_for_a_non_python(tmp_path):
    fake = tmp_path / "python.exe"
    fake.write_text("not really an interpreter", encoding="utf-8")
    assert runner.verify_interpreter(str(fake)) is False
    assert runner.verify_interpreter(str(tmp_path / "missing.exe")) is False


def test_discover_interpreter_trusts_a_saved_path_without_running_it(tmp_path):
    saved = tmp_path / "python.exe"
    saved.write_text("", encoding="utf-8")
    # a remembered, existing path is trusted as-is (no per-run verification overhead)
    assert runner.discover_interpreter(str(saved), str(tmp_path)) == str(saved)


def test_discover_interpreter_none_when_saved_missing_and_no_working_venv(tmp_path):
    assert runner.discover_interpreter(str(tmp_path / "gone.exe"), str(tmp_path)) is None


def test_find_interpreter_returns_first_existing_file(tmp_path):
    real = tmp_path / "python.exe"
    real.write_text("", encoding="utf-8")
    missing = str(tmp_path / "nope.exe")
    assert runner.find_interpreter([missing, str(real)]) == str(real)
    assert runner.find_interpreter([missing]) is None
    # a directory is not an interpreter
    assert runner.find_interpreter([str(tmp_path)]) is None


def test_output_paths_are_under_the_given_dir():
    paths = runner.output_paths(os.path.join("C:", "run42"))
    assert paths["results"].endswith("results.json")
    assert paths["status"].endswith("status.json")
    assert paths["report"].endswith("report.html")
    assert all(os.path.join("C:", "run42") in p for p in paths.values())


def test_settings_round_trip_and_missing_dir_is_empty(tmp_path):
    runner.save_settings(str(tmp_path), {"interpreter": "C:/py/python.exe", "last_donor": "d.json"})
    s = runner.load_settings(str(tmp_path))
    assert s["interpreter"] == "C:/py/python.exe"
    assert s["last_donor"] == "d.json"
    # no config yet -> empty dict, never a crash
    assert runner.load_settings(str(tmp_path / "does-not-exist")) == {}


def test_run_match_end_to_end_is_terminal_free(tmp_path):
    """The whole orchestration path: options -> subprocess -> the three artifacts on disk, with no
    shell/terminal involved. Uses this interpreter (the signed venv pytest runs under) as the engine."""
    from steelreuse.resources import sample_path

    out = tmp_path / "run"
    opts = {"donor": str(sample_path("donor.json")), "demand": str(sample_path("demand.json"))}
    res = runner.run_match(sys.executable, opts, str(out))

    assert res["ok"], res["stderr"]
    assert os.path.exists(res["paths"]["status"])
    assert os.path.exists(res["paths"]["report"])
    with open(res["paths"]["results"], encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["schema_version"] == 2
    assert "assignments" in data


def test_build_review_command_has_all_artifacts():
    cmd = runner.build_review_command("py.exe", {"donor": "d.json"}, "/out")
    assert cmd[:3] == ["py.exe", "-m", "steelreuse.validate_extraction"]
    assert "d.json" in cmd
    joined = " ".join(cmd)
    assert "--review-json" in joined and "--report" in joined
    assert "--pda-report" in joined and "--pda-out" in joined


def test_build_review_command_passes_pda_when_present():
    cmd = runner.build_review_command("py.exe", {"donor": "d.json", "pda": "a.csv"}, "/out")
    assert "--pda" in cmd and "a.csv" in cmd
