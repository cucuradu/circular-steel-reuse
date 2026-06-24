"""Donor-row mismatch log (Roadmap §1.2): 100% of donor rows classified, with a reason.

Acceptance: every donor row appears in the log exactly once with a classification
(mapped / fuzzy / unknown / quarantined) and a reason; the log + rule versions surface in the
evidence package.
"""

import json

from steelreuse.cli import main
from steelreuse.core import rules
from steelreuse.core.audit import assess_supply
from steelreuse.core.mismatch import build_mismatch_log, mismatch_summary
from steelreuse.core.sections import load_default_catalog, resolve_members
from steelreuse.evidence import build_evidence_package
from steelreuse.pipeline import run_pipeline
from steelreuse.resources import sample_path
from steelreuse.schema import ExtractedMember


def _crafted_donor():
    """One member per classification, so every bucket is exercised deterministically."""
    return [
        ExtractedMember(id="m_mapped", raw_section="IPE300", role="beam", length_mm=6000),
        ExtractedMember(id="m_fuzzy", raw_section="W18X54", role="beam", length_mm=6000),
        ExtractedMember(id="m_unknown", raw_section="Mystery Truss 9000", role="beam",
                        length_mm=6000),
        # Mapped name, but the audit rejects condition D -> quarantined.
        ExtractedMember(id="m_quar", raw_section="IPE330", role="beam", length_mm=6000,
                        condition_grade="D", verification_status="mill_cert"),
    ]


def test_mismatch_log_classifies_every_donor_row_once_with_a_reason():
    cat = load_default_catalog()
    members = _crafted_donor()
    validation = resolve_members(members, cat)
    audit = assess_supply(members)
    rows = build_mismatch_log(members, validation, audit)

    assert len(rows) == len(members)
    assert sorted(r["id"] for r in rows) == sorted(m.id for m in members)  # each exactly once
    by = {r["id"]: r for r in rows}
    assert by["m_mapped"]["classification"] == "mapped"
    assert by["m_fuzzy"]["classification"] == "fuzzy"
    assert by["m_unknown"]["classification"] == "unknown"
    assert by["m_quar"]["classification"] == "quarantined"
    # Every row carries a non-empty, human-readable reason.
    assert all(r["reason"] for r in rows)
    assert "no acceptable verification basis" in by["m_quar"]["reason"] \
        or "condition D" in by["m_quar"]["reason"]
    assert "no catalogue section" in by["m_unknown"]["reason"]
    assert "fuzzy name match" in by["m_fuzzy"]["reason"]


def test_mismatch_summary_accounts_for_all():
    cat = load_default_catalog()
    members = _crafted_donor()
    validation = resolve_members(members, cat)
    audit = assess_supply(members)
    s = mismatch_summary(build_mismatch_log(members, validation, audit))
    assert s["n_donor_rows"] == 4
    assert s["mapped"] + s["fuzzy"] + s["unknown"] + s["quarantined"] == 4
    assert s["accounts_for_all"]


def test_outcome_marks_reused_vs_unused():
    """Admitted donors get an outcome from the match; quarantined/fuzzy/unknown stay blank."""
    donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
    res = run_pipeline(donor, demand)
    by = {r["id"]: r for r in res.mismatch_log}
    reused_ids = {a.supply_id for a in res.match.assignments}
    for mid, r in by.items():
        if mid in reused_ids:
            assert r["outcome"] == "reused" and r["classification"] == "mapped"
        if r["classification"] in ("fuzzy", "unknown", "quarantined"):
            assert r["outcome"] == ""


def test_reused_and_unused_reasons_explain_the_outcome():
    """A mapped donor's reason must say WHAT HAPPENED (reused vs not used), not merely how its name
    resolved -- reused and unused rows must not read identically."""
    donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
    res = run_pipeline(donor, demand)
    reused_ids = {a.supply_id for a in res.match.assignments}
    unused_mapped = [r for r in res.mismatch_log
                     if r["classification"] == "mapped" and r["id"] not in reused_ids]
    reused = [r for r in res.mismatch_log if r["id"] in reused_ids]
    assert reused and unused_mapped, "demo run should have both reused and unused mapped donors"
    for r in reused:
        assert r["reason"].startswith("reused by the match")
    for r in unused_mapped:
        assert "not used" in r["reason"]
    # The two outcomes must produce visibly different reasons (the user's complaint).
    assert reused[0]["reason"] != unused_mapped[0]["reason"]


def test_evidence_package_carries_rules_and_mismatch():
    donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
    res = run_pipeline(donor, demand)
    pkg = build_evidence_package(res, donor_path=donor, demand_paths=[demand])

    # Rule versions named in the package.
    assert pkg["run"]["ruleset_version"] == rules.RULESET_VERSION
    assert pkg["rules"]["ruleset_version"] == rules.RULESET_VERSION
    table_names = {t["name"] for t in pkg["rules"]["tables"]}
    assert {"material_grades", "condition_knockdown"} <= table_names
    assert pkg["rules"]["carbon_factors"]["version"]

    # Mismatch log accounts for 100% of donor rows.
    assert len(pkg["mismatch_log"]) == len(res.donor.members)
    summ = pkg["mismatch_summary"]
    assert summ["accounts_for_all"]
    assert summ["n_donor_rows"] == len(res.donor.members)
    for r in pkg["mismatch_log"]:
        assert r["classification"] in {"mapped", "fuzzy", "unknown", "quarantined"}
        assert r["reason"]


def test_demo_cli_writes_rules_and_mismatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ev = tmp_path / "evidence.json"
    mm = tmp_path / "mismatch.csv"
    rc = main(["--demo", "--out", str(tmp_path / "r.html"),
               "--evidence-out", str(ev), "--mismatch-csv", str(mm)])
    assert rc == 0
    pkg = json.loads(ev.read_text(encoding="utf-8"))
    assert pkg["rules"]["ruleset_version"]
    assert pkg["mismatch_summary"]["accounts_for_all"]
    # The CSV export carries one row per donor member with a reason column.
    assert mm.exists()
    lines = mm.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].split(",")[:6] == ["id", "raw_section", "canonical", "method",
                                       "classification", "reason"]
    assert len(lines) - 1 == len(pkg["mismatch_log"])  # header + one row per donor
