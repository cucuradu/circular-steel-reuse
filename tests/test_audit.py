"""Pre-demolition audit (PDA) layer: knockdown derivation, quarantine, provenance, wiring.

Key invariant exercised here: a donor with *no* audit data behaves exactly as before (admitted at
the run default), so the audit feature is opt-in and never changes legacy results.
"""

import pytest

from steelreuse.core.audit import (
    MIN_KNOCKDOWN,
    apply_audit,
    assess_member,
    assess_supply,
    load_audit_csv,
    recoverable_length,
)
from steelreuse.core.sections import load_catalog
from steelreuse.llm.report import build_report_context, render_html
from steelreuse.pipeline import build_supply, run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def _m(mid="D1", **kw):
    kw.setdefault("length_mm", 7000)
    return ExtractedMember(id=mid, role="beam", raw_section="IPE300", **kw)


# --- knockdown derivation -------------------------------------------------------------------------

def test_no_audit_data_admits_at_default_knockdown():
    d = assess_member(_m(), default_knockdown=0.8)
    assert d.admitted and d.audited is False
    assert d.knockdown == 0.8  # the run default flows straight through (backward compatible)


def test_mill_cert_full_strength():
    d = assess_member(_m(verification_status="mill_cert", condition_grade="A"))
    assert d.admitted and d.knockdown == pytest.approx(1.0)


def test_documented_and_visual_attract_grade_knockdown():
    assert assess_member(_m(verification_status="documented")).knockdown == pytest.approx(0.95)
    assert assess_member(_m(verification_status="visual_only")).knockdown == pytest.approx(0.90)


def test_condition_and_verification_compound():
    # condition B (0.95) x documented (0.95) = 0.9025
    d = assess_member(_m(verification_status="documented", condition_grade="B"))
    assert d.admitted and d.knockdown == pytest.approx(0.9025)


def test_explicit_knockdown_overrides_derivation():
    d = assess_member(_m(verification_status="mill_cert", condition_grade="A", knockdown=0.7))
    assert d.admitted and d.knockdown == pytest.approx(0.7)


# --- quarantine -----------------------------------------------------------------------------------

def test_unverified_is_quarantined_by_default():
    d = assess_member(_m(verification_status="unverified", condition_grade="A"))
    assert not d.admitted and "verification" in d.reason


def test_unverified_admitted_when_explicitly_allowed():
    d = assess_member(_m(verification_status="unverified", condition_grade="A"),
                      include_unverified=True)
    assert d.admitted and d.knockdown <= 0.85  # knowingly admitted -> conservative


def test_condition_D_is_quarantined_even_if_verified():
    d = assess_member(_m(verification_status="mill_cert", condition_grade="D"))
    assert not d.admitted and "unsuitable" in d.reason


def test_condition_without_verification_is_treated_unverified():
    d = assess_member(_m(condition_grade="B"))  # surveyed condition but no grade basis
    assert not d.admitted


def test_knockdown_below_floor_quarantines():
    d = assess_member(_m(verification_status="mill_cert", knockdown=MIN_KNOCKDOWN - 0.05))
    assert not d.admitted


# --- recoverable length ---------------------------------------------------------------------------

def test_recoverable_length_falls_back_to_physical_length():
    assert recoverable_length(_m(length_mm=7000)) == 7000.0
    assert recoverable_length(_m(length_mm=7000, recoverable_length_mm=6500)) == 6500.0


# --- summary --------------------------------------------------------------------------------------

def test_assess_supply_counts_and_average():
    members = [
        _m("A", verification_status="mill_cert", condition_grade="A"),     # admit 1.0
        _m("B", verification_status="documented", condition_grade="B"),    # admit 0.9025
        _m("C", verification_status="unverified"),                          # quarantine
        _m("D", verification_status="mill_cert", condition_grade="D"),     # quarantine
        _m("E"),                                                            # no audit data
    ]
    s = assess_supply(members)
    assert s.present and s.n_audited == 4 and s.n_admitted == 2 and s.n_quarantined == 2
    assert s.avg_knockdown == pytest.approx((1.0 + 0.9025) / 2, abs=1e-3)
    assert s.verification_counts["mill_cert"] == 2


# --- CSV loader -----------------------------------------------------------------------------------

def test_load_and_apply_audit_csv(tmp_path):
    csv = tmp_path / "pda.csv"
    csv.write_text(
        "id,condition_grade,verification_status,knockdown,recoverable_length_mm,defects\n"
        "D1,A,mill_cert,,6800,minor pitting\n"
        "D2,C,coupon_tested,0.8,,bowed flange\n",
        encoding="utf-8",
    )
    by_id = load_audit_csv(csv)
    assert by_id["D1"]["verification_status"] == "mill_cert"
    assert by_id["D1"]["recoverable_length_mm"] == 6800.0
    assert by_id["D2"]["knockdown"] == 0.8
    members = [_m("D1"), _m("D2"), _m("D3")]
    assert apply_audit(members, by_id) == 2
    assert members[0].verification_status == "mill_cert"
    assert members[2].verification_status is None  # untouched


# --- pipeline wiring ------------------------------------------------------------------------------

def _donor(members):
    return ExtractedModel(kind="donor", members=members)


def test_build_supply_quarantines_and_threads_knockdown(cat):
    donor = _donor([
        _m("A", verification_status="mill_cert", condition_grade="A"),
        _m("B", verification_status="visual_only", condition_grade="C"),  # 0.90*0.85 = 0.765
        _m("C", verification_status="unverified"),                         # quarantined
    ])
    supply, _report, audit = build_supply(donor, cat)
    ids = {s.id: s for s in supply}
    assert set(ids) == {"A", "B"}                       # C quarantined out of supply
    assert ids["A"].knockdown == pytest.approx(1.0)
    assert ids["B"].knockdown == pytest.approx(0.765)
    assert audit.n_quarantined == 1


def test_include_unverified_admits_quarantined(cat):
    donor = _donor([_m("C", verification_status="unverified")])
    supply, _r, audit = build_supply(donor, cat, include_unverified=True)
    assert {s.id for s in supply} == {"C"} and audit.n_quarantined == 0


def test_legacy_donor_unaffected(cat):
    # No audit fields anywhere -> audit summary reports nothing present, supply unchanged.
    donor = _donor([_m("A"), _m("B")])
    supply, _r, audit = build_supply(donor, cat, knockdown=1.0)
    assert len(supply) == 2 and audit.present is False
    assert all(s.knockdown == 1.0 for s in supply)


def test_run_pipeline_with_pda_csv(cat, tmp_path):
    donor = _donor([
        _m("D1", verification_status="mill_cert", condition_grade="A"),
        _m("D2"),  # will be quarantined via the CSV (unverified)
    ])
    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="N1", role="beam", raw_section="IPE300", spans_mm=[6000]),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)
    pda = tmp_path / "pda.csv"
    pda.write_text("id,verification_status\nD2,unverified\n", encoding="utf-8")

    res = run_pipeline(str(dp), str(mp), pda_csv=str(pda))
    assert res.audit.present and res.audit.n_quarantined == 1
    assert res.supply_count == 1  # only the mill-cert member survives


def test_report_context_and_html_include_provenance(cat, tmp_path):
    donor = _donor([_m("D1", verification_status="documented", condition_grade="B")])
    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="N1", role="beam", raw_section="IPE300", spans_mm=[6000]),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)
    res = run_pipeline(str(dp), str(mp))
    ctx = build_report_context(res)
    assert ctx["audit_present"] is True
    assert ctx["audit_audited"] == 1
    html = render_html(ctx, "narrative", "deterministic")
    assert "Pre-demolition audit" in html and "documented" in html


def test_report_context_no_audit_when_legacy(cat, tmp_path):
    donor = _donor([_m("D1")])
    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="N1", role="beam", raw_section="IPE300", spans_mm=[6000]),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)
    ctx = build_report_context(run_pipeline(str(dp), str(mp)))
    assert ctx["audit_present"] is False
    assert "Pre-demolition audit" not in render_html(ctx, "n", "deterministic")
