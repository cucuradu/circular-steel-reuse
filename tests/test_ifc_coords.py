"""IFC coordinate export — the Revit-free path now resolves member axis endpoints.

Each test builds a *tiny* IFC4 model in-memory (no fixtures on disk, no Revit) with explicit
placements and axis/extrusion geometry, extracts it, and checks that ``start_xyz``/``end_xyz`` land
within 1 mm of hand-computed global coordinates — in the same convention as the pyRevit extractor
(global model coordinates, millimetres). The honest no-geometry path (coords left ``None`` + a note)
is checked too, and an end-to-end test proves an IFC mini-model now engages the frame solver.

Skips cleanly if the optional ``[bim]`` extra (ifcopenshell) is not installed.
"""

import math

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from steelreuse.ifc_extract import extract_ifc  # noqa: E402


# --- tiny IFC builder -----------------------------------------------------------------------------
# Built with raw create_entity so each placement/curve is explicit and hand-checkable.

def _new_model(length_unit="MILLI"):
    """An IFC4 file with a project, the SI length unit (MILLI metres or metres), and a Model/Axis ctx."""
    m = ifcopenshell.file(schema="IFC4")
    if length_unit is None:  # plain metres (no prefix)
        unit = m.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    else:
        unit = m.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix=length_unit, Name="METRE")
    assignment = m.create_entity("IfcUnitAssignment", Units=[unit])
    origin = m.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    world = m.create_entity("IfcAxis2Placement3D", Location=origin)
    ctx = m.create_entity(
        "IfcGeometricRepresentationContext",
        ContextType="Model", CoordinateSpaceDimension=3, Precision=1e-5,
        WorldCoordinateSystem=world,
    )
    m.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="CoordTest",
                    UnitsInContext=assignment, RepresentationContexts=[ctx])
    axis_ctx = m.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Axis", ContextType="Model",
        ParentContext=ctx, TargetView="GRAPH_VIEW",
    )
    return m, ctx, axis_ctx


def _point(m, xyz):
    return m.create_entity("IfcCartesianPoint", Coordinates=tuple(float(c) for c in xyz))


def _placement(m, location=(0.0, 0.0, 0.0), axis=None, ref=None, rel_to=None):
    """An IfcLocalPlacement wrapping an IfcAxis2Placement3D (optionally nested under ``rel_to``)."""
    kwargs = {"Location": _point(m, location)}
    if axis is not None:
        kwargs["Axis"] = m.create_entity("IfcDirection", DirectionRatios=tuple(float(c) for c in axis))
    if ref is not None:
        kwargs["RefDirection"] = m.create_entity(
            "IfcDirection", DirectionRatios=tuple(float(c) for c in ref))
    a2p = m.create_entity("IfcAxis2Placement3D", **kwargs)
    return m.create_entity("IfcLocalPlacement", PlacementRelTo=rel_to, RelativePlacement=a2p)


def _axis_rep(m, axis_ctx, start, end):
    """An 'Axis' IfcShapeRepresentation holding a 2-point IfcPolyline (local member centreline)."""
    poly = m.create_entity("IfcPolyline", Points=[_point(m, start), _point(m, end)])
    return m.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=axis_ctx, RepresentationIdentifier="Axis",
        RepresentationType="Curve3D", Items=[poly],
    )


def _body_extrusion_rep(m, ctx, base_placement_origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0),
                        depth=3000.0):
    """A 'Body' IfcShapeRepresentation with one vertical IfcExtrudedAreaSolid (a column run)."""
    profile = m.create_entity(
        "IfcRectangleProfileDef", ProfileType="AREA", XDim=100.0, YDim=100.0,
        Position=m.create_entity(
            "IfcAxis2Placement2D",
            Location=m.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0))),
    )
    pos = m.create_entity("IfcAxis2Placement3D", Location=_point(m, base_placement_origin))
    solid = m.create_entity(
        "IfcExtrudedAreaSolid", SweptArea=profile, Position=pos,
        ExtrudedDirection=m.create_entity("IfcDirection", DirectionRatios=direction),
        Depth=float(depth),
    )
    return m.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=ctx, RepresentationIdentifier="Body",
        RepresentationType="SweptSolid", Items=[solid],
    )


def _product(m, ifc_class, name, placement, reps):
    pds = m.create_entity("IfcProductDefinitionShape", Representations=reps) if reps else None
    return m.create_entity(
        ifc_class, GlobalId=ifcopenshell.guid.new(), Name=name,
        ObjectPlacement=placement, Representation=pds,
    )


def _dist(a, b):
    return math.dist(a, b)


# --- tests ----------------------------------------------------------------------------------------

def test_axis_beam_along_x_at_offset(tmp_path):
    """6 m beam along local +X, placed at (1000, 2000, 3000) mm -> global endpoints in mm."""
    m, ctx, axis_ctx = _new_model("MILLI")
    placement = _placement(m, location=(1000.0, 2000.0, 3000.0))
    rep = _axis_rep(m, axis_ctx, (0.0, 0.0, 0.0), (6000.0, 0.0, 0.0))
    _product(m, "IfcBeam", "B1", placement, [rep])
    path = tmp_path / "beam.ifc"
    m.write(str(path))

    model = extract_ifc(str(path))
    beam = model.members[0]
    assert beam.role == "beam"
    assert _dist(beam.start_xyz, (1000.0, 2000.0, 3000.0)) < 1.0
    assert _dist(beam.end_xyz, (7000.0, 2000.0, 3000.0)) < 1.0
    # length consistency: |end - start| ~= length_mm
    assert abs(_dist(beam.start_xyz, beam.end_xyz) - beam.length_mm) < 1.0
    assert abs(beam.length_mm - 6000.0) < 1.0


def test_rotated_placement_beam(tmp_path):
    """Beam axis local +X, but the placement is rotated 90deg about Z (RefDirection = +Y).

    A local +X 4000 mm axis at origin offset (500, 0, 0) becomes a global +Y run.
    """
    m, ctx, axis_ctx = _new_model("MILLI")
    placement = _placement(m, location=(500.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0), ref=(0.0, 1.0, 0.0))
    rep = _axis_rep(m, axis_ctx, (0.0, 0.0, 0.0), (4000.0, 0.0, 0.0))
    _product(m, "IfcBeam", "B2", placement, [rep])
    path = tmp_path / "rot.ifc"
    m.write(str(path))

    model = extract_ifc(str(path))
    beam = model.members[0]
    assert _dist(beam.start_xyz, (500.0, 0.0, 0.0)) < 1.0
    # local X -> global Y after the 90deg rotation
    assert _dist(beam.end_xyz, (500.0, 4000.0, 0.0)) < 1.0
    assert abs(_dist(beam.start_xyz, beam.end_xyz) - 4000.0) < 1.0


def test_nested_placement(tmp_path):
    """Storey placement (0,0,3000) + element placement (1000,1000,0) compose additively."""
    m, ctx, axis_ctx = _new_model("MILLI")
    storey = _placement(m, location=(0.0, 0.0, 3000.0))
    placement = _placement(m, location=(1000.0, 1000.0, 0.0), rel_to=storey)
    rep = _axis_rep(m, axis_ctx, (0.0, 0.0, 0.0), (2000.0, 0.0, 0.0))
    _product(m, "IfcBeam", "B3", placement, [rep])
    path = tmp_path / "nest.ifc"
    m.write(str(path))

    beam = extract_ifc(str(path)).members[0]
    assert _dist(beam.start_xyz, (1000.0, 1000.0, 3000.0)) < 1.0
    assert _dist(beam.end_xyz, (3000.0, 1000.0, 3000.0)) < 1.0


def test_column_from_extrusion(tmp_path):
    """Point-placed 3 m column: only a vertical Body extrusion -> base + top in mm (mirrors Revit)."""
    m, ctx, axis_ctx = _new_model("MILLI")
    placement = _placement(m, location=(5000.0, 5000.0, 0.0))
    rep = _body_extrusion_rep(m, ctx, direction=(0.0, 0.0, 1.0), depth=3000.0)
    _product(m, "IfcColumn", "C1", placement, [rep])
    path = tmp_path / "col.ifc"
    m.write(str(path))

    col = extract_ifc(str(path)).members[0]
    assert col.role == "column"
    assert _dist(col.start_xyz, (5000.0, 5000.0, 0.0)) < 1.0
    assert _dist(col.end_xyz, (5000.0, 5000.0, 3000.0)) < 1.0
    assert abs(_dist(col.start_xyz, col.end_xyz) - 3000.0) < 1.0


def test_metres_length_unit(tmp_path):
    """File length unit = metres: a 6 m local axis must convert to 6000 mm global coordinates."""
    m, ctx, axis_ctx = _new_model(length_unit=None)  # metres, no prefix
    placement = _placement(m, location=(1.0, 0.0, 0.0))  # 1 m offset
    rep = _axis_rep(m, axis_ctx, (0.0, 0.0, 0.0), (6.0, 0.0, 0.0))  # 6 m beam
    _product(m, "IfcBeam", "B4", placement, [rep])
    path = tmp_path / "metres.ifc"
    m.write(str(path))

    beam = extract_ifc(str(path)).members[0]
    assert _dist(beam.start_xyz, (1000.0, 0.0, 0.0)) < 1.0
    assert _dist(beam.end_xyz, (7000.0, 0.0, 0.0)) < 1.0
    assert abs(beam.length_mm - 6000.0) < 1.0


def test_no_geometry_leaves_none_and_notes(tmp_path):
    """A member with no resolvable axis: coords stay None and a note records why (honesty rule)."""
    m, ctx, axis_ctx = _new_model("MILLI")
    placement = _placement(m, location=(0.0, 0.0, 0.0))
    _product(m, "IfcBeam", "B5", placement, reps=None)  # no representation at all
    path = tmp_path / "nogeo.ifc"
    m.write(str(path))

    beam = extract_ifc(str(path)).members[0]
    assert beam.start_xyz is None
    assert beam.end_xyz is None
    assert "axis" in beam.notes.lower()


def test_frame_engages_on_ifc_model(tmp_path):
    """End-to-end: an IFC-extracted one-bay portal runs through the frame solver (res.frame.ok).

    Two 3 m columns + one 6 m beam connecting their tops, written to IFC, extracted (donor + demand),
    then run through run_pipeline with frame_analysis on an AreaLoadModel. Proves the IFC path now
    feeds the geometry-dependent frame analysis the Revit path enabled.
    """
    pytest.importorskip("Pynite")
    from steelreuse.core.loads import AreaLoadModel
    from steelreuse.pipeline import run_pipeline

    def _portal(kind):
        m, ctx, axis_ctx = _new_model("MILLI")
        steel = m.create_entity("IfcMaterial", Name="Steel S275")

        def _assign(prod):
            m.create_entity(
                "IfcRelAssociatesMaterial", GlobalId=ifcopenshell.guid.new(),
                RelatedObjects=[prod], RelatingMaterial=steel)

        # columns at x=0 and x=6000, rising 0->3000
        for cx in (0.0, 6000.0):
            pl = _placement(m, location=(cx, 0.0, 0.0))
            rep = _body_extrusion_rep(m, ctx, direction=(0.0, 0.0, 1.0), depth=3000.0)
            col = _product(m, "IfcColumn", f"COL{cx:.0f}", pl, [rep])
            col.ObjectType = "HE 300 B"
            _assign(col)
        # beam spanning the two column tops at z=3000
        pl = _placement(m, location=(0.0, 0.0, 3000.0))
        rep = _axis_rep(m, axis_ctx, (0.0, 0.0, 0.0), (6000.0, 0.0, 0.0))
        beam = _product(m, "IfcBeam", "BEAM", pl, [rep])
        beam.ObjectType = "IPE300"
        _assign(beam)

        path = tmp_path / f"{kind}.ifc"
        m.write(str(path))
        model = extract_ifc(str(path), kind=kind)
        out = tmp_path / f"{kind}.json"
        model.save(out)
        return str(out)

    donor_path = _portal("donor")
    demand_path = _portal("demand")

    res = run_pipeline(
        donor_path, demand_path, loads=AreaLoadModel(),
        frame_analysis=True,
    )
    assert res.frame is not None
    assert res.frame.ok
    # the beam + both columns connected into a real frame (>= 3 members, >= 4 nodes)
    assert res.frame.member_count >= 3
    assert res.frame.node_count >= 4
