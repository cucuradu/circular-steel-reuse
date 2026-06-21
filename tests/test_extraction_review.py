# tests/test_extraction_review.py
"""Tests for the extraction review core (the single source of truth for both reports)."""

from steelreuse.core.sections import load_default_catalog
from steelreuse.extraction_review import extraction_review
from steelreuse.schema import ExtractedMember, ExtractedModel

CAT = load_default_catalog()


def _model(*members):
    return ExtractedModel(kind="donor", members=list(members))


def test_clean_mapped_member_has_no_issues():
    # A genuinely clean member is mapped, graded, has coords AND carries admitting audit data;
    # an unaudited member always trips NOT_AUDITED (info), so audit fields are required for "clean".
    m = ExtractedMember(id="D1", role="beam", raw_section="IPE300",
                        material_grade="S275", start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0],
                        condition_grade="A", verification_status="mill_cert")
    rv = extraction_review(_model(m), CAT)
    mr = rv.members[0]
    assert mr.mapping_method == "exact"
    assert mr.section == "IPE300"
    assert mr.issues == []
    assert rv.mapped == 1 and rv.unknown == 0


def test_unknown_section_is_flagged_error():
    m = ExtractedMember(id="D2", role="beam", raw_section="BAR JOIST 18K3",
                        material_grade="S275", start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0])
    rv = extraction_review(_model(m), CAT)
    codes = [c for c, _ in rv.members[0].issues]
    assert "UNKNOWN_SECTION" in codes
    assert rv.members[0].worst_severity == "error"
    assert rv.unknown == 1


def test_missing_grade_and_no_coords_flagged():
    m = ExtractedMember(id="D3", role="beam", raw_section="IPE300")  # no grade, no coords
    rv = extraction_review(_model(m), CAT)
    codes = [c for c, _ in rv.members[0].issues]
    assert "MISSING_GRADE" in codes
    assert "NO_COORDS" in codes


def test_condition_d_quarantined():
    m = ExtractedMember(id="D4", role="beam", raw_section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0],
                        condition_grade="D", verification_status="visual_only")
    rv = extraction_review(_model(m), CAT)
    codes = [c for c, _ in rv.members[0].issues]
    assert "QUARANTINED_CONDITION_D" in codes
    assert rv.members[0].admitted is False
    assert rv.quarantined == 1 and rv.audited == 1


def test_unverified_quarantined_and_audit_counts():
    m = ExtractedMember(id="D5", role="beam", raw_section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0],
                        condition_grade="A", verification_status="unverified")
    rv = extraction_review(_model(m), CAT)
    codes = [c for c, _ in rv.members[0].issues]
    assert "QUARANTINED_UNVERIFIED" in codes


def test_not_audited_member_flagged_info():
    m = ExtractedMember(id="D6", role="beam", raw_section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0])
    rv = extraction_review(_model(m), CAT)
    codes = [c for c, _ in rv.members[0].issues]
    assert "NOT_AUDITED" in codes
    assert rv.audited == 0


def test_to_dict_carries_coverage_and_color():
    m = ExtractedMember(id="D7", role="beam", raw_section="NOPE")
    d = extraction_review(_model(m), CAT).to_dict()
    assert d["schema_version"] == 1
    assert d["coverage"]["total"] == 1
    assert d["members"][0]["color"] == [214, 39, 40]  # error -> red
    assert d["coverage"]["issue_counts"]["UNKNOWN_SECTION"] == 1


def test_explicit_knockdown_below_floor_flags_low_knockdown():
    # An explicit auditor knockdown under the 0.30 floor is quarantined as LOW_KNOCKDOWN (not the
    # generic unverified path) -- this exercises the reason_code discriminant from core.audit.
    m = ExtractedMember(id="D8", role="beam", raw_section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0],
                        condition_grade="A", verification_status="mill_cert", knockdown=0.1)
    rv = extraction_review(_model(m), CAT)
    codes = [c for c, _ in rv.members[0].issues]
    assert "LOW_KNOCKDOWN" in codes
    assert "QUARANTINED_UNVERIFIED" not in codes
    assert rv.members[0].admitted is False
    assert rv.quarantined == 1


def test_pda_csv_merge_changes_review(tmp_path):
    # An audit CSV passed via pda= is merged onto members before review, exactly like a match run.
    m = ExtractedMember(id="D9", role="beam", raw_section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0])
    assert extraction_review(_model(m), CAT).audited == 0  # nothing audited without the CSV
    csv_path = tmp_path / "audit.csv"
    csv_path.write_text("id,condition_grade,verification_status\nD9,D,visual_only\n",
                        encoding="utf-8")
    rv = extraction_review(_model(m), CAT, pda=str(csv_path))
    codes = [c for c, _ in rv.members[0].issues]
    assert "QUARANTINED_CONDITION_D" in codes
    assert rv.audited == 1 and rv.quarantined == 1


def test_geometry_confirmed_section_reports_geometry_method():
    # An unrecognised name whose measured dimensions match exactly one catalog row is identified by
    # geometry (method "geometry"), not left UNKNOWN -- IPE300's real dims, under a mystery name.
    m = ExtractedMember(id="D10", role="beam", raw_section="MYSTERY BEAM",
                        material_grade="S275", start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0],
                        h_mm=300.0, b_mm=150.0, tf_mm=10.7, tw_mm=7.1)
    rv = extraction_review(_model(m), CAT)
    mr = rv.members[0]
    assert mr.mapping_method == "geometry"
    assert mr.section == "IPE300"
    assert "UNKNOWN_SECTION" not in [c for c, _ in mr.issues]
