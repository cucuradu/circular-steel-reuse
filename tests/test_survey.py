# tests/test_survey.py
import csv
import io
import json

from steelreuse.schema import ExtractedMember, ExtractedModel
from steelreuse.survey import (
    SURVEY_COLUMNS,
    load_survey,
    normalize_survey_value,
    survey_template_csv,
    survey_template_rows,
)


def _model():
    return ExtractedModel(kind="donor", members=[
        ExtractedMember(id="1", unique_id="g1", mark="B-1", role="beam", level="L2",
                        raw_section="IPE300", length_mm=6000.0,
                        start_xyz=[0, 0, 3000], end_xyz=[6000, 0, 3000]),
    ])


def test_template_rows_prefill_context_blank_audit():
    row = survey_template_rows(_model())[0]
    assert row["unique_id"] == "g1" and row["mark"] == "B-1"
    assert row["level"] == "L2" and row["raw_section"] == "IPE300"
    assert row["length_mm"] == 6000.0
    assert row["recoverable_length_mm"] == 6000.0   # defaults to length
    assert row["condition_grade"] == "" and row["verification_status"] == ""
    assert row["connection_type"] == ""


def test_template_csv_has_fixed_header_order():
    text = survey_template_csv(_model())
    header = next(csv.reader(io.StringIO(text)))
    assert header == SURVEY_COLUMNS


def test_normalize_verification_synonyms():
    assert normalize_survey_value("verification_status", "Mill Certificate") == "mill_cert"
    assert normalize_survey_value("verification_status", "coupon test") == "coupon_tested"
    assert normalize_survey_value("verification_status", "drawings") == "documented"
    assert normalize_survey_value("verification_status", "visual") == "visual_only"
    assert normalize_survey_value("verification_status", "") == "unverified"


def test_normalize_condition_synonyms():
    assert normalize_survey_value("condition_grade", "good") == "A"
    assert normalize_survey_value("condition_grade", "light corrosion") == "B"
    assert normalize_survey_value("condition_grade", "section loss") == "C"
    assert normalize_survey_value("condition_grade", "unsuitable") == "D"
    assert normalize_survey_value("condition_grade", "B") == "B"   # already canonical


def test_normalize_connection_and_numbers():
    assert normalize_survey_value("connection_type", "pinned") == "bolted"
    assert normalize_survey_value("connection_type", "moment") == "welded"
    assert normalize_survey_value("knockdown", "0.9") == 0.9
    assert normalize_survey_value("knockdown", "bad") is None


def test_load_survey_csv_with_alias_headers_and_key_fallback(tmp_path):
    # aliases: guid->unique_id, grade->condition, cert->verification; blank row has no key -> skipped
    p = tmp_path / "s.csv"
    p.write_text("GUID,Grade,Cert,Mark\n"
                 "g1,good,mill certificate,B-1\n"
                 ",,,\n",
                 encoding="utf-8")
    recs = load_survey(str(p))
    assert "g1" in recs
    assert recs["g1"]["condition_grade"] == "A"
    assert recs["g1"]["verification_status"] == "mill_cert"


def test_load_survey_json(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([{"unique_id": "g2", "condition": "poor"}]), encoding="utf-8")
    recs = load_survey(str(p))
    assert recs["g2"]["condition_grade"] == "D"


def test_load_survey_key_order_uses_mark_when_no_id(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("mark,condition\nC-9,good\n", encoding="utf-8")
    recs = load_survey(str(p))
    assert "C-9" in recs       # fell back to mark as the key


def test_load_survey_col_map_override(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("ref,state\ng5,good\n", encoding="utf-8")
    recs = load_survey(str(p), col_map={"ref": "unique_id", "state": "condition_grade"})
    assert recs["g5"]["condition_grade"] == "A"
