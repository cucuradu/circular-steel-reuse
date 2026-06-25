"""Tests for the scenario-sweep orchestration (lib/steelreuse_sweep.py).

Pure planning / collecting / ranking, plus the injected-runner thread pool -- run under CPython here
exactly as under IronPython in Revit. No real engine is spawned: run_grid is driven with a fake run
function that writes a results.json, so the whole plan -> run -> collect -> rank loop is exercised
headless.
"""

import importlib
import json
import os
import sys

_LIBDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                                       "SteelReuse.extension", "lib"))
if _LIBDIR not in sys.path:
    sys.path.insert(0, _LIBDIR)

sweep = importlib.import_module("steelreuse_sweep")


# --- planning -------------------------------------------------------------------------------------

def test_lean_strips_finalist_only_addons_but_keeps_pareto():
    opts = {"donor": "d.json", "objective": "co2", "donor_value": True, "verify_match": True,
            "disposition": True, "pareto": True}
    out = sweep.lean(opts)
    assert "donor_value" not in out and "verify_match" not in out and "disposition" not in out
    assert out["pareto"] is True                      # cheap, kept for the per-point trade-off
    assert opts.get("donor_value") is True            # original untouched (copy, not mutate)


def test_grid_size_and_expand_are_the_cartesian_product():
    axes = [("objective", ["co2", "members", "mass"]), ("min_util", [0.0, 0.6])]
    assert sweep.grid_size(axes) == 6
    points = sweep.expand_grid({"donor": "d.json"}, axes)
    assert len(points) == 6
    # fixed base carried onto every point; first axis varies slowest (deterministic order)
    assert all(p["donor"] == "d.json" for p in points)
    assert (points[0]["objective"], points[0]["min_util"]) == ("co2", 0.0)
    assert (points[1]["objective"], points[1]["min_util"]) == ("co2", 0.6)
    assert (points[2]["objective"], points[2]["min_util"]) == ("members", 0.0)


def test_parse_values_types_each_axis_and_skips_junk():
    assert sweep.parse_values("objective", "co2, members , mass") == ["co2", "members", "mass"]
    assert sweep.parse_values("min_util", "0.0, 0.5, x, 0.7") == [0.0, 0.5, 0.7]   # 'x' skipped
    assert sweep.parse_values("knockdown", "0.9,0.85") == [0.9, 0.85]
    # max_distinct_sections: 'none' -> no cap (None), numbers coerced to int
    assert sweep.parse_values("max_distinct_sections", "none, 6, 10") == [None, 6, 10]
    assert sweep.parse_values("objective", "") == []


def test_no_axes_is_a_single_run():
    assert sweep.grid_size([]) == 1
    points = sweep.expand_grid({"donor": "d.json"}, [])
    assert len(points) == 1 and points[0] == {"donor": "d.json"}


def test_point_id_and_label_use_only_swept_axes():
    axes = [("objective", ["members"]), ("min_util", [0.6]), ("max_distinct_sections", [None])]
    opts = {"objective": "members", "min_util": 0.6, "max_distinct_sections": None, "donor": "d"}
    assert sweep.point_label(axes, opts) == "objective=members, min_util=0.6, max_distinct_sections=None"
    # slugged: dot -> 'p', None -> 'none', so the folder name is filesystem-safe and stable
    assert sweep.point_id(axes, opts) == "objective-members__min_util-0p6__max_distinct_sections-none"
    assert sweep.point_id([], {}) == "run"


def test_plan_lays_out_lean_opts_and_per_point_folders():
    axes = [("objective", ["co2", "members"])]
    rows = sweep.plan({"donor": "d.json", "donor_value": True}, axes, os.path.join("root", "sweep1"))
    assert [r["id"] for r in rows] == ["objective-co2", "objective-members"]
    assert rows[0]["out_dir"] == os.path.join("root", "sweep1", "objective-co2")
    assert "donor_value" not in rows[0]["opts"]       # lean-ified
    assert rows[1]["opts"]["objective"] == "members"


# --- execution ------------------------------------------------------------------------------------

def _fake_run(reused_by_objective):
    """A run_fn that writes a results.json into out_dir, faking KPIs keyed off the point's objective."""
    def run_fn(interpreter, opts, out_dir):
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        reused = reused_by_objective[opts["objective"]]
        kpis = {"slots": 10, "reused": reused, "co2_saved_kg": reused * 100.0,
                "mass_reused_kg": reused * 50.0, "distinct_sections": reused,
                "reuse_rate_pct": reused * 10.0, "proven_optimal": True,
                "solver_status": "Optimal", "objective": opts["objective"]}
        with open(os.path.join(out_dir, sweep.RESULTS_NAME), "w") as fh:
            json.dump({"schema_version": 2, "kpis": kpis, "unfilled": []}, fh)
        return {"ok": True, "paths": {"results": os.path.join(out_dir, sweep.RESULTS_NAME)}}
    return run_fn


def test_run_grid_runs_every_point_and_reports_progress(tmp_path):
    axes = [("objective", ["co2", "members", "mass"])]
    rows = sweep.plan({"donor": "d.json", "objective": "co2"}, axes, str(tmp_path))
    seen = []
    results = sweep.run_grid(rows, interpreter="py", run_fn=_fake_run({"co2": 5, "members": 7, "mass": 4}),
                             max_workers=2, on_done=lambda d, t, row, res: seen.append((d, t)))
    assert all(r["ok"] for r in results)              # one result per point, in plan order
    assert len(results) == 3
    assert sorted(seen) == [(1, 3), (2, 3), (3, 3)]   # progress fired once per point, total stable


def test_default_workers_leaves_a_core_for_revit(monkeypatch):
    monkeypatch.setenv("NUMBER_OF_PROCESSORS", "8")
    assert sweep.default_workers() == 7
    monkeypatch.setenv("NUMBER_OF_PROCESSORS", "1")
    assert sweep.default_workers() == 1               # never below 1


# --- collecting + ranking -------------------------------------------------------------------------

def test_collect_reads_kpis_and_flags_missing_runs(tmp_path):
    axes = [("objective", ["co2", "members"])]
    rows = sweep.plan({"donor": "d.json", "objective": "co2"}, axes, str(tmp_path))
    # run only the first point; the second has no results.json on disk
    sweep.run_grid(rows[:1], interpreter="py", run_fn=_fake_run({"co2": 5}), max_workers=1)
    records = sweep.collect(rows)
    assert records[0]["ok"] and records[0]["reused"] == 5 and records[0]["unfilled"] == 5
    assert records[0]["out_dir"] == rows[0]["out_dir"]      # board uses it for "Open folder"
    assert records[1]["ok"] is False and records[1]["reused"] is None


def _rec(rid, reused, co2, mass, distinct):
    return {"id": rid, "reused": reused, "co2_saved_kg": co2, "mass_reused_kg": mass,
            "distinct_sections": distinct, "unfilled": 10 - reused}


def test_rank_orders_best_first_and_pushes_failures_last():
    recs = [_rec("a", 5, 500.0, 250.0, 5), _rec("b", 7, 400.0, 200.0, 6),
            {"id": "broken", "reused": None}]
    assert [r["id"] for r in sweep.rank(recs, "reused")] == ["b", "a", "broken"]
    # distinct_sections is a 'min' metric: fewer is better
    assert [r["id"] for r in sweep.rank(recs, "distinct_sections")][:2] == ["a", "b"]


def test_pareto_front_keeps_only_genuine_tradeoffs():
    # 'a' wins CO2, 'b' wins members -> both on the front; 'c' is beaten by 'a' on both -> dominated.
    a = _rec("a", 5, 500.0, 250.0, 5)
    b = _rec("b", 7, 400.0, 200.0, 6)
    c = _rec("c", 4, 450.0, 220.0, 7)
    metrics = [("reused", "max"), ("co2_saved_kg", "max")]
    front_ids = sorted(r["id"] for r in sweep.pareto_front([a, b, c], metrics))
    assert front_ids == ["a", "b"]


def test_pareto_front_excludes_failed_runs():
    a = _rec("a", 5, 500.0, 250.0, 5)
    broken = {"id": "broken", "reused": None, "co2_saved_kg": None}
    metrics = [("reused", "max"), ("co2_saved_kg", "max")]
    assert [r["id"] for r in sweep.pareto_front([a, broken], metrics)] == ["a"]


def test_mark_front_flags_records_in_place_with_default_metrics():
    a = _rec("a", 5, 500.0, 250.0, 5)   # wins co2
    b = _rec("b", 7, 400.0, 200.0, 6)   # wins members
    c = _rec("c", 4, 450.0, 220.0, 7)   # dominated by a on all four default metrics
    recs = [a, b, c]
    out = sweep.mark_front(recs)
    assert out is recs                                   # mutates + returns the same list
    assert a["on_front"] is True and b["on_front"] is True and c["on_front"] is False
