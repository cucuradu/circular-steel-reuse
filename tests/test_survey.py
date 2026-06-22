# tests/test_survey.py
import csv
import io

from steelreuse.schema import ExtractedMember, ExtractedModel
from steelreuse.survey import SURVEY_COLUMNS, survey_template_csv, survey_template_rows


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
