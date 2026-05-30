"""Phase 7 test: the IFC extraction path (Revit-free) round-trips into the JSON schema.

Builds a tiny IFC4 model in-memory with IfcOpenShell, then extracts it and checks the result.
"""

import ifcopenshell
from ifcopenshell.api import run

from steelreuse.core.sections import load_catalog, resolve_members
from steelreuse.ifc_extract import extract_ifc


def _build_sample_ifc(path):
    model = ifcopenshell.file(schema="IFC4")
    run("root.create_entity", model, ifc_class="IfcProject", name="SampleIFC")
    metre = run("unit.add_si_unit", model, unit_type="LENGTHUNIT")  # explicit metres
    run("unit.assign_unit", model, units=[metre])

    steel = run("material.add_material", model, name="Steel S275")

    beam = run("root.create_entity", model, ifc_class="IfcBeam", name="B1")
    beam.ObjectType = "IPE300"
    run("material.assign_material", model, products=[beam], material=steel)
    qto = run("pset.add_qto", model, product=beam, name="Qto_BeamBaseQuantities")
    run("pset.edit_qto", model, qto=qto, properties={"Length": 6.2})

    col = run("root.create_entity", model, ifc_class="IfcColumn", name="C1")
    col.ObjectType = "HE 300 B"
    run("material.assign_material", model, products=[col], material=steel)
    qto2 = run("pset.add_qto", model, product=col, name="Qto_ColumnBaseQuantities")
    run("pset.edit_qto", model, qto=qto2, properties={"Length": 3.5})

    model.write(str(path))


def test_ifc_extraction_roundtrip(tmp_path):
    ifc_path = tmp_path / "sample.ifc"
    _build_sample_ifc(ifc_path)

    model = extract_ifc(str(ifc_path), kind="donor")
    assert model.source == "ifc"
    by_role = {m.role: m for m in model.members}
    assert set(by_role) == {"beam", "column"}

    beam = by_role["beam"]
    assert beam.raw_section == "IPE300"
    assert beam.material_grade == "S275"
    assert beam.length_mm == 6200.0   # 6.2 m -> mm

    # and the section mapping layer accepts the extracted names
    cat = load_catalog()
    report = resolve_members(model.members, cat)
    assert len(report.unknown) == 0
    assert by_role["column"].section == "HEB300"
