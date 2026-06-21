"""Tests for the extraction-validation helper (steelreuse.validate_extraction)."""

import json

from steelreuse.schema import ExtractedMember, ExtractedModel
from steelreuse.validate_extraction import main, summarize

_MODEL = (
    '{"kind": "donor", "members": ['
    '{"id": "b1", "role": "beam", "raw_section": "IPE300", "length_mm": 6000},'
    '{"id": "c1", "role": "column", "raw_section": "HEB200", "length_mm": 3000}]}'
)


def _write(tmp_path):
    p = tmp_path / "model.json"
    p.write_text(_MODEL, encoding="utf-8")
    return str(p)


def test_expect_match_exits_zero(tmp_path, capsys):
    assert main([_write(tmp_path), "--expect", "2"]) == 0
    assert "OK" in capsys.readouterr().out


def test_expect_mismatch_exits_one(tmp_path, capsys):
    assert main([_write(tmp_path), "--expect", "5"]) == 1
    assert "MISMATCH" in capsys.readouterr().err


def test_schedule_row_count_match(tmp_path, capsys):
    sched = tmp_path / "schedule.csv"
    sched.write_text("Type,Length\nIPE300,6000\nHEB200,3000\n", encoding="utf-8")
    assert main([_write(tmp_path), "--schedule", str(sched)]) == 0


def test_missing_file_exits_one(tmp_path, capsys):
    assert main([str(tmp_path / "nope.json")]) == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_summarize_counts_match_legacy_semantics():
    """Regression baseline: must stay numerically identical after summarize() is refactored
    to delegate to the review core (extraction_review)."""
    members = [
        ExtractedMember(id="A", role="beam", raw_section="IPE300",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0]),
        ExtractedMember(id="B", role="column", raw_section="HEB300",
                        start_xyz=[0, 0, 0], end_xyz=[0, 0, 3000]),
        ExtractedMember(id="C", role="beam", raw_section="BAR JOIST 18K3"),  # unknown, no coords
    ]
    s = summarize(ExtractedModel(kind="donor", members=members))
    assert s["total"] == 3
    assert s["roles"] == {"beam": 2, "column": 1}
    assert s["mapped"] == 2          # IPE300 + HEB300
    assert s["unknown"] == 1         # total - mapped (fuzzy would also count here)
    assert s["with_coords"] == 2
    assert s["columns"] == 1
    assert s["columns_with_coords"] == 1


def test_summarize_fuzzy_quarantined_counts_as_unknown():
    """The subtle reason summarize uses total - mapped (not rv.unknown): a fuzzy-quarantined
    near-miss keeps section=None and must count as 'unknown' in this legacy field."""
    members = [
        ExtractedMember(id="A", role="beam", raw_section="IPE300"),    # exact -> mapped
        ExtractedMember(id="B", role="beam", raw_section="IPE-300"),   # fuzzy near-miss, quarantined
    ]
    s = summarize(ExtractedModel(kind="donor", members=members))
    assert s["total"] == 2
    assert s["mapped"] == 1
    assert s["unknown"] == 1   # fuzzy member counts here, even though rv.unknown alone would be 0


def _write_model(tmp_path):
    members = [
        {"id": "A", "role": "beam", "raw_section": "IPE300", "material_grade": "S275",
         "start_xyz": [0, 0, 0], "end_xyz": [6000, 0, 0]},
        {"id": "B", "role": "beam", "raw_section": "BAR JOIST 18K3"},
    ]
    p = tmp_path / "donor.json"
    p.write_text(json.dumps({"kind": "donor", "members": members}), encoding="utf-8")
    return p


def test_cli_writes_all_review_artifacts(tmp_path):
    donor = _write_model(tmp_path)
    report = tmp_path / "problems.html"
    pda_report = tmp_path / "pda.html"
    review_json = tmp_path / "review.json"
    pda_out = tmp_path / "audit.csv"
    rc = main([str(donor), "--report", str(report), "--pda-report", str(pda_report),
               "--review-json", str(review_json), "--pda-out", str(pda_out)])
    assert rc == 0
    assert "UNKNOWN_SECTION" in report.read_text(encoding="utf-8")
    assert "pre-demolition audit" in pda_report.read_text(encoding="utf-8").lower()
    assert json.loads(review_json.read_text(encoding="utf-8"))["coverage"]["total"] == 2
    assert "condition_grade" in pda_out.read_text(encoding="utf-8")


def test_cli_pda_merge_changes_review(tmp_path):
    donor = _write_model(tmp_path)
    audit = tmp_path / "in.csv"
    audit.write_text("id,condition_grade,verification_status\nA,D,visual_only\n", encoding="utf-8")
    review_json = tmp_path / "review.json"
    main([str(donor), "--pda", str(audit), "--review-json", str(review_json)])
    data = json.loads(review_json.read_text(encoding="utf-8"))
    a = next(m for m in data["members"] if m["id"] == "A")
    assert any(c == "QUARANTINED_CONDITION_D" for c, _ in a["issues"])
