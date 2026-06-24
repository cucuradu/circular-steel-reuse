# tests/test_inventory_sheet.py
import csv
import io

import pytest

from steelreuse.inventory_sheet import (
    INVENTORY_COLUMNS,
    inventory_template_csv,
    inventory_template_rows,
    load_inventory_model,
    member_from_record,
    write_inventory_template,
)
from steelreuse.pipeline import load_model_file
from steelreuse.schema import ExtractedModel

# --- blank template ------------------------------------------------------------------------------

def test_template_csv_header_and_one_example_row():
    text = inventory_template_csv()
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == INVENTORY_COLUMNS          # header in fixed order
    assert len(rows) == 2                         # header + exactly one worked example
    example = dict(zip(INVENTORY_COLUMNS, rows[1], strict=True))
    assert example["verification_status"] == "unverified"   # the conservative flag is shown
    assert example["raw_section"] == "IPE 300"
    assert example["length_mm"] == "8000"


def test_template_example_round_trips_into_a_member():
    row = inventory_template_rows()[0]
    member = member_from_record(row)
    assert member is not None
    assert member.id == "D1" and member.role == "beam"
    assert member.length_mm == 8000.0
    assert member.recoverable_length_mm == 7600.0
    assert member.verification_status == "unverified"
    assert member.material_grade == "S275"


def test_write_inventory_template_csv(tmp_path):
    target = tmp_path / "t.csv"
    write_inventory_template(str(target))
    model = load_inventory_model(str(target))
    assert model.kind == "donor"
    assert len(model.members) == 1 and model.members[0].id == "D1"


def test_write_inventory_template_xlsx(tmp_path):
    pytest.importorskip("openpyxl")
    target = tmp_path / "t.xlsx"
    write_inventory_template(str(target))
    assert target.exists()
    model = load_inventory_model(str(target))   # data sheet read back unchanged
    assert len(model.members) == 1 and model.members[0].raw_section == "IPE 300"


# --- reader: aliases, coercion, role, blank rows -------------------------------------------------

def test_load_csv_with_alias_headers(tmp_path):
    p = tmp_path / "inv.csv"
    p.write_text(
        "Piece,Profile,Steel Grade,Length (mm),Member Type,Provenance\n"
        "P1,UB 305x165x40,S355,7500,Column,mill cert\n",
        encoding="utf-8",
    )
    model = load_inventory_model(str(p), kind="donor")
    m = model.members[0]
    assert m.id == "P1"
    assert m.raw_section == "UB 305x165x40"
    assert m.material_grade == "S355"
    assert m.length_mm == 7500.0
    assert m.role == "column"                       # "Column" -> canonical
    assert m.verification_status == "mill_cert"     # synonym normalised


def test_role_synonyms_and_unknown():
    assert member_from_record({"id": "a", "role": "girder", "length_mm": "1"}).role == "beam"
    assert member_from_record({"id": "b", "role": "post", "length_mm": "1"}).role == "column"
    assert member_from_record({"id": "c", "role": "diagonal", "length_mm": "1"}).role == "brace"
    assert member_from_record({"id": "d", "role": "widget", "length_mm": "1"}).role == "unknown"


def test_blank_rows_skipped(tmp_path):
    p = tmp_path / "inv.csv"
    p.write_text("id,section,length_mm\nD1,IPE200,4000\n,,\n  ,,\n", encoding="utf-8")
    model = load_inventory_model(str(p))
    assert len(model.members) == 1


def test_id_falls_back_to_mark_then_section():
    # no id, but a mark -> mark is the id
    m = member_from_record({"mark": "B-9", "raw_section": "IPE100", "length_mm": "2000"})
    assert m.id == "B-9"
    # no id and no mark, but a section/length -> kept with a placeholder id, not dropped
    m2 = member_from_record({"raw_section": "IPE100", "length_mm": "2000"})
    assert m2 is not None and m2.id == "(unnamed)"


def test_recoverable_defaults_to_length_when_blank():
    m = member_from_record({"id": "x", "raw_section": "IPE100", "length_mm": "6000"})
    # ExtractedMember leaves recoverable_length_mm None; the audit layer treats that as = length.
    assert m.length_mm == 6000.0
    assert m.recoverable_length_mm is None


def test_col_map_override(tmp_path):
    p = tmp_path / "inv.csv"
    p.write_text("ref,sec,len\nZ1,HE200B,3000\n", encoding="utf-8")
    model = load_inventory_model(
        str(p), col_map={"ref": "id", "sec": "raw_section", "len": "length_mm"})
    assert model.members[0].id == "Z1" and model.members[0].length_mm == 3000.0


# --- pipeline dispatch ---------------------------------------------------------------------------

def test_load_model_file_dispatches_csv_vs_json(tmp_path):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("id,section,length_mm\nD1,IPE300,8000\n", encoding="utf-8")
    model = load_model_file(str(csv_path), "donor")
    assert isinstance(model, ExtractedModel) and model.source == "spreadsheet"
    assert model.kind == "donor" and model.members[0].length_mm == 8000.0

    json_path = tmp_path / "d.json"
    ExtractedModel(kind="demand", members=[]).save(str(json_path))
    jmodel = load_model_file(str(json_path), "demand")
    assert jmodel.kind == "demand" and jmodel.source != "spreadsheet"
