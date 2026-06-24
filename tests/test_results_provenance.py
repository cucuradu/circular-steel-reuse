"""Roadmap §1.2 surfaced through the Results button (no new button).

Covers the whole chain: the engine embeds rule versions + the donor mismatch log into the
results.json contract; the Run Match runner emits the --evidence-out / --mismatch-csv flags so a run
is self-contained; and the headless Results view-model + HTML view surface both — all testable here
because the pyRevit libs are pure + stdlib-only (IronPython-safe).
"""

import importlib.util
import os

from steelreuse.pipeline import run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel
from steelreuse.writeback import build_results

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension", "SteelReuse.extension", "lib")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_LIB, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load("steelreuse_runner")
view = _load("steelreuse_results_view")
model = _load("steelreuse_panel_model")


def _run(tmp_path):
    """One clean reuse + one quarantined (condition D) + one unmapped donor -> all four buckets."""
    donor = ExtractedModel(kind="donor", source="pyrevit", members=[
        ExtractedMember(id="D1", role="beam", raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D2", role="beam", raw_section="W Shapes W18x55", length_mm=7000,
                        condition_grade="D", verification_status="documented"),
        ExtractedMember(id="D3", role="beam", raw_section="Mystery Joist 9000", length_mm=7000),
    ])
    demand = ExtractedModel(kind="demand", source="pyrevit", members=[
        ExtractedMember(id="N1", role="beam", raw_section="W Shapes W16x26", spans_mm=[6000]),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)
    return run_pipeline(str(dp), str(mp), steel_only_demand=True)


# -- engine: the results.json contract carries rule versions + the mismatch log -------------------

def test_results_contract_carries_rules_and_mismatch(tmp_path):
    out = build_results(_run(tmp_path))
    assert "rules" in out and "mismatch" in out
    assert out["rules"]["ruleset_version"]
    assert out["rules"]["carbon_factors_version"]
    assert {t["name"] for t in out["rules"]["tables"]} >= {"material_grades", "condition_knockdown"}

    summary = out["mismatch"]["summary"]
    rows = out["mismatch"]["rows"]
    assert summary["accounts_for_all"]
    assert summary["n_donor_rows"] == 3 == len(rows)
    classes = {r["id"]: r["classification"] for r in rows}
    assert classes["D1"] == "mapped"
    assert classes["D2"] == "quarantined"
    assert classes["D3"] == "unknown"
    assert all(r["reason"] for r in rows)  # 100% of donor rows have a reason


# -- runner: Run Match now always emits the evidence package + mismatch CSV ------------------------

def test_build_command_emits_evidence_and_mismatch():
    out_dir = os.path.join("C:", "out")
    cmd = runner.build_command("py", {"donor": "d.json", "demand": "m.json"}, out_dir)

    def _pair(flag):
        return cmd[cmd.index(flag) + 1]

    assert "--evidence-out" in cmd and "--mismatch-csv" in cmd
    assert _pair("--evidence-out").endswith("evidence.json")
    assert _pair("--mismatch-csv").endswith("mismatch.csv")
    assert out_dir in _pair("--evidence-out") and out_dir in _pair("--mismatch-csv")


def test_output_paths_include_the_new_artifacts():
    paths = runner.output_paths(os.path.join("C:", "out"))
    assert paths["evidence"].endswith("evidence.json")
    assert paths["mismatch"].endswith("mismatch.csv")


# -- headless view-model: parses the new blocks ----------------------------------------------------

def test_panel_model_parses_rules_and_mismatch():
    data = {
        "schema_version": 2, "kpis": {}, "assignments": [],
        "rules": {"ruleset_version": "1.0.0", "tables": [{"name": "material_grades", "version": "1.0.0"}],
                  "carbon_factors_version": "1.0.0"},
        "mismatch": {"summary": {"n_donor_rows": 2, "mapped": 1, "fuzzy": 0, "unknown": 0,
                                 "quarantined": 1, "accounts_for_all": True},
                     "rows": [{"id": "D1", "classification": "mapped", "reason": "exact"},
                              {"id": "D2", "classification": "quarantined", "reason": "condition D"}]},
    }
    v = model.parse(data)
    assert v.rules["ruleset_version"] == "1.0.0"
    assert v.has_mismatch
    assert v.mismatch["summary"]["accounts_for_all"]


def test_panel_model_tolerates_old_runs_without_blocks():
    v = model.parse({"schema_version": 2, "kpis": {}, "assignments": []})
    assert v.rules == {} and v.mismatch == {}
    assert not v.has_mismatch


# -- HTML view: the rule stamp + provenance table render -------------------------------------------

def test_results_view_renders_rules_and_mismatch(tmp_path):
    html = view.render_results_html(build_results(_run(tmp_path)))
    assert "Rule data:" in html and "ruleset v" in html
    assert "Donor provenance (mismatch log)" in html
    assert "accounted for" in html
    # The quarantined + unknown donors appear with their reasons in the table.
    assert "D2" in html and "D3" in html


def test_results_view_without_blocks_is_unchanged():
    # An old results.json (no rules/mismatch) renders without the new sections, no crash.
    html = view.render_results_html({"schema_version": 2, "kpis": {"reused": 0, "slots": 0},
                                     "assignments": []})
    assert "Rule data:" not in html
    assert "Donor provenance" not in html
