# tests/test_schema.py
from steelreuse.schema import ExtractedMember, ExtractedModel


def test_member_carries_unique_id_and_mark():
    m = ExtractedMember(id="123", unique_id="abcd-ef01", mark="B-12", raw_section="IPE300")
    assert m.unique_id == "abcd-ef01"
    assert m.mark == "B-12"


def test_unique_id_and_mark_roundtrip_through_json(tmp_path):
    m = ExtractedMember(id="1", unique_id="guid-1", mark="C-3", raw_section="HEB300")
    model = ExtractedModel(kind="donor", members=[m])
    p = tmp_path / "d.json"
    model.save(p)
    back = ExtractedModel.load(p)
    assert back.members[0].unique_id == "guid-1"
    assert back.members[0].mark == "C-3"


def test_old_extraction_without_new_fields_still_loads():
    m = ExtractedMember.from_dict({"id": "1", "raw_section": "IPE300"})
    assert m.unique_id is None and m.mark is None


def test_member_carries_connection_fields():
    m = ExtractedMember(id="1", raw_section="IPE300",
                        connection_type="welded", connection_condition="C",
                        deconstructability="hard")
    assert m.connection_type == "welded"
    assert m.connection_condition == "C"
    assert m.deconstructability == "hard"


def test_connection_fields_default_none():
    m = ExtractedMember(id="1", raw_section="IPE300")
    assert m.connection_type is None
    assert m.connection_condition is None
    assert m.deconstructability is None
