"""Tests for the reuse value + suitability generator (no demand model needed)."""

import subprocess
import sys

from steelreuse.core.value_case import (
    MarketParams,
    MemberValueCase,
    ValueCaseResult,
    value_case,
)
from steelreuse.schema import ExtractedMember, ExtractedModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _m(**kw) -> ExtractedMember:
    kw.setdefault("length_mm", 6000.0)
    section = kw.setdefault("section", "IPE300")
    # value_case re-maps raw_section -> section, so keep raw_section consistent with the intended
    # section (otherwise the mapping would overwrite it).
    kw.setdefault("raw_section", section)
    kw.setdefault("material_grade", "S275")
    # A reuse-ready member needs a test-verified grade + sound condition; default to that so the
    # base helper lands REUSE and individual tests opt into REVIEW/SCRAP by overriding.
    kw.setdefault("verification_status", "mill_cert")
    kw.setdefault("condition_grade", "A")
    return ExtractedMember(id=kw.pop("id", "m1"), **kw)


def _donor(*members) -> ExtractedModel:
    return ExtractedModel(kind="donor", members=list(members))


def _params(**kw) -> MarketParams:
    return MarketParams(**kw)


# ---------------------------------------------------------------------------
# MarketParams
# ---------------------------------------------------------------------------

def test_default_params_are_sensible():
    p = MarketParams()
    assert p.scrap_price_per_tonne < p.reclaimed_price_per_tonne
    assert p.co2_price_per_tonne == 0.0


# ---------------------------------------------------------------------------
# Per-member value: scrap, reclaimed, premium
# ---------------------------------------------------------------------------

def test_scrap_value_formula():
    p = _params(scrap_price_per_tonne=200.0)
    row = value_case(_donor(_m()), params=p).rows[0]
    assert abs(row.scrap_value_gbp - row.mass_kg / 1000.0 * 200.0) < 0.01


def test_reclaimed_value_formula():
    p = _params(reclaimed_price_per_tonne=1000.0)
    row = value_case(_donor(_m()), params=p).rows[0]
    assert abs(row.reclaimed_value_gbp - row.mass_kg / 1000.0 * 1000.0) < 0.01


def test_premium_is_reclaimed_minus_scrap():
    p = _params(scrap_price_per_tonne=240.0, reclaimed_price_per_tonne=950.0)
    row = value_case(_donor(_m()), params=p).rows[0]
    assert abs(row.reuse_premium_gbp - (row.reclaimed_value_gbp - row.scrap_value_gbp)) < 0.01
    assert row.reuse_premium_gbp > 0  # reclaimed always beats scrap


# ---------------------------------------------------------------------------
# Verdict: reuse suitability (mapping + audit), no economics
# ---------------------------------------------------------------------------

def test_verified_sound_member_is_REUSE():
    row = value_case(_donor(_m(verification_status="mill_cert", condition_grade="A"))).rows[0]
    assert row.verdict == "REUSE"
    assert row.reclaimed_value_gbp > 0
    assert "reuse-ready" in row.note.lower()


def test_unaudited_member_is_REVIEW_needs_test():
    # No grade documentation at all -> reusable but must be coupon-tested first.
    m = _m(verification_status=None, condition_grade=None)
    row = value_case(_donor(m)).rows[0]
    assert row.verdict == "REVIEW"
    assert row.reclaimed_value_gbp > 0          # still a reuse candidate (premium shown)
    assert "coupon" in row.note.lower() or "test" in row.note.lower()


def test_visual_only_grade_is_REVIEW():
    m = _m(verification_status="visual_only", condition_grade="A")
    assert value_case(_donor(m)).rows[0].verdict == "REVIEW"


def test_condition_C_is_REVIEW_even_when_grade_verified():
    m = _m(verification_status="mill_cert", condition_grade="C")
    row = value_case(_donor(m)).rows[0]
    assert row.verdict == "REVIEW"
    assert "inspect" in row.note.lower() or "condition c" in row.note.lower()


def test_condition_D_is_SCRAP_with_scrap_value():
    # Quarantined: can't be reused, but still has scrap value (you can always shred it).
    m = _m(verification_status="mill_cert", condition_grade="D")
    row = value_case(_donor(m)).rows[0]
    assert row.verdict == "SCRAP"
    assert not row.audit_admitted
    assert row.reclaimed_value_gbp == 0.0       # cannot be reused
    assert row.reuse_premium_gbp == 0.0
    assert row.scrap_value_gbp > 0.0            # still worth something as scrap
    assert row.co2_saved_kg == 0.0              # not reused -> no carbon saved


def test_foundations_excluded():
    found = ExtractedMember(id="F1", raw_section="Concrete Pile Cap", section="IPE300",
                            length_mm=2000.0, category="Structural Foundations")
    result = value_case(_donor(found, _m(id="B1")))
    assert {r.id for r in result.rows} == {"B1"}
    assert result.skipped_breakdown.get("foundation") == 1


def test_unmapped_member_skipped_by_default():
    m = ExtractedMember(id="x", raw_section="UNKNOWN999", length_mm=6000.0)
    result = value_case(_donor(m))
    assert result.rows == []
    assert result.skipped_total == 1
    assert result.skipped_breakdown.get("unmapped") == 1


def test_unmapped_member_listed_when_requested():
    m = ExtractedMember(id="x", raw_section="UNKNOWN999", length_mm=6000.0)
    row = value_case(_donor(m), include_unmapped=True).rows[0]
    assert row.verdict == "SCRAP"
    assert row.section is None
    assert row.mass_kg == 0.0
    assert "UNKNOWN999" in row.note


# ---------------------------------------------------------------------------
# CO2
# ---------------------------------------------------------------------------

def test_co2_value_zero_when_price_zero():
    assert value_case(_donor(_m()), params=_params(co2_price_per_tonne=0.0)).rows[0].co2_value_gbp == 0.0


def test_co2_value_nonzero_with_ets_price():
    row = value_case(_donor(_m()), params=_params(co2_price_per_tonne=75.0)).rows[0]
    assert row.co2_saved_kg > 0 and row.co2_value_gbp > 0.0


def test_co2_saved_only_for_reusable_members():
    reuse = value_case(_donor(_m())).rows[0]
    scrap = value_case(_donor(_m(condition_grade="D"))).rows[0]
    assert reuse.co2_saved_kg > 0.0
    assert scrap.co2_saved_kg == 0.0


# ---------------------------------------------------------------------------
# Sorting and aggregates
# ---------------------------------------------------------------------------

def test_rows_sorted_descending_by_premium():
    members = [_m(id="short", length_mm=1000.0), _m(id="long", length_mm=9000.0)]
    premiums = [r.reuse_premium_gbp for r in value_case(_donor(*members)).rows]
    assert premiums == sorted(premiums, reverse=True)


def test_totals_cover_reuse_and_review_only():
    reuse_m = _m(id="ok")                            # REUSE
    scrap_m = _m(id="bad", condition_grade="D")      # SCRAP
    r = value_case(_donor(reuse_m, scrap_m))
    reusable_sum = sum(row.reuse_premium_gbp for row in r.rows if row.verdict != "SCRAP")
    assert abs(r.total_reuse_premium_gbp - reusable_sum) < 0.01
    # scrap mass is tracked separately from reusable mass
    assert r.scrap_mass_kg > 0 and r.reusable_mass_kg > 0


def test_counts_add_up():
    members = [_m(id="a"), _m(id="b", condition_grade="D"), _m(id="c", verification_status=None)]
    r = value_case(_donor(*members))
    assert r.reuse_count + r.review_count + r.scrap_count == len(r.rows)


def test_mass_split_reusable_vs_scrap():
    r = value_case(_donor(_m(id="a"), _m(id="b", condition_grade="D")))
    reusable = sum(row.mass_kg for row in r.rows if row.verdict != "SCRAP")
    scrap = sum(row.mass_kg for row in r.rows if row.verdict == "SCRAP")
    assert abs(r.reusable_mass_kg - reusable) < 0.01
    assert abs(r.scrap_mass_kg - scrap) < 0.01


# ---------------------------------------------------------------------------
# Reuse score
# ---------------------------------------------------------------------------

def test_reuse_score_in_range():
    members = [_m(id=f"m{i}", length_mm=float(3000 + i * 1000)) for i in range(5)]
    for row in value_case(_donor(*members)).rows:
        if row.section:
            assert 0.0 <= row.reuse_score <= 1.0


def test_reuse_score_is_populated_not_silently_zero():
    # Regression: reuse_score is a stdlib heuristic and must work on a base (zero-dep) install.
    # Before the clustering lazy-import fix, importing it pulled numpy/sklearn and silently failed,
    # zeroing every score. A standardized, long member must score clearly above zero.
    members = [_m(id=f"m{i}", length_mm=8000.0) for i in range(4)]  # 4x identical IPE300
    assert all(r.reuse_score > 0.0 for r in value_case(_donor(*members)).rows)


def test_reuse_score_rewards_standardization():
    repeated = [_m(id=f"r{i}", section="IPE300", length_mm=6000.0) for i in range(5)]
    singleton = _m(id="solo", section="IPE400", length_mm=6000.0)
    by_id = {r.id: r for r in value_case(_donor(*(repeated + [singleton]))).rows}
    assert by_id["r0"].reuse_score > by_id["solo"].reuse_score


# ---------------------------------------------------------------------------
# End-to-end on bundled sample
# ---------------------------------------------------------------------------

def test_end_to_end_bundled_sample():
    from steelreuse.resources import sample_path

    donor = ExtractedModel.load(sample_path("donor.json"))
    result = value_case(donor)

    assert isinstance(result, ValueCaseResult)
    assert len(result.rows) + result.skipped_total == len(donor.members)
    assert result.reuse_count + result.review_count + result.scrap_count == len(result.rows)
    for row in result.rows:
        assert isinstance(row, MemberValueCase)
        assert row.verdict in ("REUSE", "REVIEW", "SCRAP")
        assert row.note  # every listed member explains its verdict


# ---------------------------------------------------------------------------
# Writeback JSON
# ---------------------------------------------------------------------------

def test_writeback_schema():
    from steelreuse.writeback import build_value_case_writeback

    members = [_m(id="a"), _m(id="b", section="IPE400")]
    wb = build_value_case_writeback(value_case(_donor(*members)))

    assert wb["schema_version"] == 1
    assert wb["kind"] == "value_case"
    assert set(wb["members"]) == {"a", "b"}
    for entry in wb["members"].values():
        assert entry["status"] in ("reuse", "review", "scrap")
        assert isinstance(entry["color"], list)
        assert entry["note"]                          # the schedule needs a per-member reason
        assert "reuse_premium_gbp" in entry
        assert "verification_status" in entry
    kpis = wb["kpis"]
    assert kpis["reuse_count"] + kpis["review_count"] + kpis["scrap_count"] == len(members)
    assert "total_reuse_premium_gbp" in kpis and "reusable_mass_kg" in kpis
    assert "skipped_total" in kpis


def test_writeback_color_matches_verdict():
    from steelreuse.writeback import VALUE_CASE_COLORS, build_value_case_writeback

    wb = build_value_case_writeback(value_case(_donor(_m(id="m1"))))
    for entry in wb["members"].values():
        assert entry["color"] == list(VALUE_CASE_COLORS[entry["status"]])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_csv_export_has_expected_columns(tmp_path):
    # Exercise the real CLI CSV writer end-to-end (no duplicated reconstruction in the test).
    import csv

    csv_path = tmp_path / "out.csv"
    proc = subprocess.run(
        [sys.executable, "-m", "steelreuse.value_case_cli", "--demo", "--out-csv", str(csv_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert csv_path.is_file()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)
    for col in ("id", "section", "grade", "verdict", "note", "reuse_premium_gbp",
                "reclaimed_value_gbp", "co2_saved_kg", "verification_status"):
        assert col in header, "missing column: " + col
    assert rows and all(r["note"] for r in rows)  # every exported row carries its reason


def test_cli_demo_exits_0():
    proc = subprocess.run(
        [sys.executable, "-m", "steelreuse.value_case_cli", "--demo"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert any(v in proc.stdout for v in ("REUSE", "REVIEW", "SCRAP"))
