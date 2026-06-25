"""Evidence package (Roadmap §1.1): the package alone lets a reviewer re-check a run.

The acceptance bar, exercised here:
  * re-run verify_match from the package's recorded data -> the same verdict as the certificate;
  * the package's CO2 reconciles with MatchResult.total_co2_saved_kg, and per-assignment carbon
    re-derives to the booked saving;
  * each assignment carries its EN 1993 governing clause / utilisation / chi_LT / governing
    combination;
  * the demo CLI produces a package on disk.
"""

import json

import pytest

from steelreuse.cli import main
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import load_default_catalog
from steelreuse.evidence import (
    EVIDENCE_SCHEMA,
    build_evidence_package,
    rebuild_run,
    verify_from_package,
    write_evidence_package,
)
from steelreuse.match.optimize import verify_match
from steelreuse.pipeline import run_pipeline
from steelreuse.resources import sample_path


@pytest.fixture(scope="module")
def demo_run():
    """A real pipeline run on the bundled demo models (cutting-stock default, co2 objective)."""
    donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
    res = run_pipeline(donor, demand)
    pkg = build_evidence_package(res, donor_path=donor, demand_paths=[demand])
    return res, pkg, donor, demand


def test_package_has_schema_and_core_blocks(demo_run):
    _, pkg, _, _ = demo_run
    assert pkg["schema"] == EVIDENCE_SCHEMA
    assert pkg["tool"]["name"] == "steelreuse" and pkg["tool"]["version"]
    for key in ("inputs", "run", "assignments", "certificate", "carbon_reconciliation",
                "reconstruction", "en_constants", "headline"):
        assert key in pkg, f"missing top-level block {key!r}"
    # Inputs carry hashes so the reviewer can confirm what was fed in.
    assert pkg["inputs"]["donor"]["sha256"]
    assert pkg["inputs"]["demand"][0]["sha256"]
    assert pkg["inputs"]["catalog"]["files"], "catalogue files should be hashed"
    assert pkg["inputs"]["carbon_factors"]["steel"]["a1a3_kgco2e_per_kg"] > 0


def test_reverify_from_package_matches_certificate(demo_run):
    """Re-run verify_match from ONLY the package's recorded data -> same verdict it was built with."""
    res, pkg, _, _ = demo_run
    # The certificate stored in the package.
    cert_issues = pkg["certificate"]["verify_match_issues"]
    # Independent re-check from the reconstruction block alone.
    repackaged_issues = verify_from_package(pkg)
    assert repackaged_issues == cert_issues
    # And it agrees with verifying the live run objects directly.
    live_issues = verify_match(res.supply, res.slots, load_default_catalog(), res.match)
    assert repackaged_issues == live_issues
    assert pkg["certificate"]["verified"] == (not cert_issues)


def test_reconstruction_round_trips(demo_run):
    """rebuild_run reproduces the supply/slots/result the solve saw (ids, lengths, assignments)."""
    res, pkg, _, _ = demo_run
    supply, slots, result = rebuild_run(pkg)
    assert {s.id for s in supply} == {s.id for s in res.supply}
    assert {s.id for s in slots} == {s.id for s in res.slots}
    assert len(result.assignments) == len(res.match.assignments)
    assert result.weights == res.match.weights
    # The full load-combination envelope survives the round-trip, not just the gravity case.
    by_id = {s.id: s for s in slots}
    for orig in res.slots:
        assert len(by_id[orig.id].combinations) == len(orig.combinations)


def test_carbon_reconciles_with_match_total(demo_run):
    res, pkg, _, _ = demo_run
    recon = pkg["carbon_reconciliation"]
    assert recon["reconciles"], recon
    assert recon["sum_assignment_co2_saved_kg"] == pytest.approx(
        round(res.match.total_co2_saved_kg, 2), abs=0.06)
    # Every booked assignment saving re-derives from the avoided-new basis.
    assert recon["per_assignment_reconciles"], "a per-assignment carbon figure failed to re-derive"
    for ev in pkg["assignments"]:
        if ev["carbon"].get("available"):
            assert ev["carbon"]["reconciles"], ev


def test_assignments_carry_en_evidence(demo_run):
    _, pkg, _, _ = demo_run
    assert pkg["assignments"], "demo run should produce at least one assignment"
    for ev in pkg["assignments"]:
        en = ev["en_1993"]
        assert en["available"]
        assert en["governing_clause"]
        assert en["governing_combination"]
        assert 0.0 <= en["utilization"] <= 1.0
        assert en["f_y_Nmm2"] > 0
        assert en["section_class"] in (1, 2, 3, 4)
        # chi_LT is reported on the assignment (None only when there is no bending).
        assert "chi_LT" in ev


def test_tampered_result_is_caught_on_reverify(demo_run):
    """If the recorded result is mutated (a slot double-filled), the re-check flags it — proving the
    package is genuinely re-derived, not merely echoed."""
    _, pkg, _, _ = demo_run
    tampered = json.loads(json.dumps(pkg))  # deep copy
    assignments = tampered["reconstruction"]["result"]["assignments"]
    if len(assignments) >= 2:
        # Point a second assignment at the first's slot -> a double-fill the audit must catch.
        assignments[1]["slot_id"] = assignments[0]["slot_id"]
        issues = verify_from_package(tampered)
        assert any("more than once" in i or "improving" in i or "feasible" in i for i in issues), \
            issues


def test_demo_cli_writes_package(tmp_path, monkeypatch):
    """`steelreuse --demo` produces an evidence package on disk (acceptance: a package is produced)."""
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "r.html"
    ev = tmp_path / "evidence.json"
    rc = main(["--demo", "--out", str(out), "--evidence-out", str(ev)])
    assert rc == 0
    assert ev.exists()
    pkg = json.loads(ev.read_text(encoding="utf-8"))
    assert pkg["schema"] == EVIDENCE_SCHEMA
    assert pkg["certificate"]["verified"]
    assert pkg["carbon_reconciliation"]["reconciles"]
    # Re-verify the on-disk package end-to-end.
    assert verify_from_package(pkg) == []


def test_write_evidence_package_creates_dirs(tmp_path, demo_run):
    _, pkg, _, _ = demo_run
    target = tmp_path / "nested" / "dir" / "pkg.json"
    written = write_evidence_package(pkg, target)
    assert written == target and target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == EVIDENCE_SCHEMA


def test_package_co2_matches_hand_recompute(demo_run):
    """A reviewer reproducing the headline by hand: sum the avoided-new minus reuse/penalty terms
    from the per-assignment carbon block -> the package headline CO2."""
    _, pkg, _, _ = demo_run
    hand_total = sum(
        ev["carbon"]["net_co2_saved_kgco2e_recomputed"]
        for ev in pkg["assignments"] if ev["carbon"].get("available")
    )
    tol = 0.06 * len(pkg["assignments"]) + 0.06
    assert hand_total == pytest.approx(pkg["headline"]["co2_saved_kg"], abs=tol)


# A minimal hand-built run keeps the EN-evidence assertions independent of the demo data.
def test_small_known_run_evidence():
    from steelreuse.core.sections import load_catalog
    from steelreuse.match.optimize import DemandSlot, SupplyItem, match

    cat = load_catalog()
    M, V = AnalyticBackend().beam_span_forces(6000, 20.0)
    slot = DemandSlot(id="S0", member_id="m", role="beam", required_length_mm=6000,
                      demand=MemberDemand(My_Ed=M, Vz_Ed=V, L=6000,
                                          compression_flange_restrained=True),
                      grade="S275", design_section="IPE360")
    supply = [SupplyItem(id="d1", section="IPE360", grade="S275", length_mm=7000)]
    result = match(supply, [slot], cat)

    class _Res:  # minimal PipelineResult stand-in for build_evidence_package
        pass

    res = _Res()
    res.match = result
    res.supply = supply
    res.slots = [slot]
    res.supply_count = 1
    res.slot_count = 1
    res.donor = None

    class _PP:
        total_saved_kgco2e = 0.0
    res.passport = _PP()

    pkg = build_evidence_package(res, catalog=cat)
    assert pkg["certificate"]["verified"]
    assert pkg["carbon_reconciliation"]["reconciles"]
    ev = pkg["assignments"][0]
    assert ev["section"] == "IPE360"
    assert ev["en_1993"]["governing_clause"]  # e.g. bending_y / shear_z
    # Tier 3: per-assignment next-best alternative is recorded (the lone donor has no substitute).
    alt = pkg["assignment_alternatives"][0]
    assert alt["slot_id"] == "S0" and alt["chosen_supply_id"] == "d1"
    assert alt["alternative_supply_id"] is None and alt["n_alternatives"] == 0
    assert verify_from_package(pkg, catalog=cat) == []
