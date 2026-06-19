"""IFC ingestion (IfcOpenShell) — a Revit-free path to the same JSON schema as the pyRevit extractor.

Reads IfcBeam / IfcColumn / IfcMember elements and pulls:
  * raw_section — from the material profile name, else ObjectType, else Name;
  * material_grade — parsed (S235/S275/...) from the associated material name;
  * length_mm — from a base-quantity "Length", converted to mm via the file's unit scale;
  * start_xyz / end_xyz — the member axis endpoints in **global model coordinates, mm** (same
    convention as the pyRevit extractor), resolved from the element's ``ObjectPlacement`` chain and
    its geometric representation (see :func:`_member_axis`). These let the IFC path drive the same
    geometry features (tributary estimation, frame analysis, span verification) the Revit path does.

Unlike the pyRevit extractor (which runs inside Revit), this runs in the normal CPython environment,
so the whole pipeline can be exercised on real BIM data without Revit.

This module deliberately avoids numpy (so the IFC path stays as lightweight as the rest of the
package). The placement maths is therefore a small hand-rolled 4x4 matrix layer (``_mat_*`` below)
that mirrors :mod:`ifcopenshell.util.placement` semantics without the dependency.
"""

from __future__ import annotations

import math
import re

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uu

from .schema import ExtractedMember, ExtractedModel

_GRADE_RE = re.compile(r"S\s*(235|275|355|420|460)", re.IGNORECASE)
_CLASS_ROLE = (("IfcBeam", "beam"), ("IfcColumn", "column"), ("IfcMember", "brace"))

# --- minimal pure-Python 4x4 transform layer (row-major; no numpy) -------------------------------
# A transform is a list of 4 rows, each a list of 4 floats. Points are (x, y, z) tuples; homogeneous
# w = 1 is implied. This is enough to compose IfcLocalPlacement chains and transform axis endpoints
# to the global frame, matching ifcopenshell.util.placement (which uses numpy) for the cases the
# extractor cares about.

Vec3 = tuple[float, float, float]
Mat4 = list[list[float]]


def _identity() -> Mat4:
    return [[1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def _mat_mul(a: Mat4, b: Mat4) -> Mat4:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _mat_apply(m: Mat4, p: Vec3) -> Vec3:
    x, y, z = p
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3])


def _mat_apply_dir(m: Mat4, d: Vec3) -> Vec3:
    """Apply only the rotation part of ``m`` to a direction (ignore translation)."""
    x, y, z = d
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalize(v: Vec3) -> Vec3:
    n = _norm(v)
    return (v[0] / n, v[1] / n, v[2] / n) if n else v


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _axis2placement3d(placement) -> Mat4:
    """4x4 matrix for an IfcAxis2Placement3D (or 2D) — origin + orthonormal X/Y/Z columns.

    Follows the EN/IFC construction: Z from ``Axis`` (default +Z), X from ``RefDirection`` (default
    +X) made orthogonal to Z by Gram-Schmidt, Y = Z x X. Returns identity for unexpected inputs.
    """
    if placement is None:
        return _identity()
    loc = getattr(placement, "Location", None)
    coords = getattr(loc, "Coordinates", None) or (0.0, 0.0, 0.0)
    o = (float(coords[0]), float(coords[1]), float(coords[2]) if len(coords) > 2 else 0.0)

    axis = getattr(placement, "Axis", None)
    ref = getattr(placement, "RefDirection", None)
    z = _normalize(tuple(float(c) for c in axis.DirectionRatios) if axis else (0.0, 0.0, 1.0))
    if len(z) == 2:
        z = (z[0], z[1], 0.0)
    x_raw = tuple(float(c) for c in ref.DirectionRatios) if ref else (1.0, 0.0, 0.0)
    if len(x_raw) == 2:
        x_raw = (x_raw[0], x_raw[1], 0.0)
    # Gram-Schmidt: remove the Z component from X, then normalise.
    dot = x_raw[0] * z[0] + x_raw[1] * z[1] + x_raw[2] * z[2]
    x = (x_raw[0] - dot * z[0], x_raw[1] - dot * z[1], x_raw[2] - dot * z[2])
    if _norm(x) < 1e-12:  # X parallel to Z (degenerate) — pick any axis orthogonal to Z
        trial = _cross((1.0, 0.0, 0.0), z)
        if _norm(trial) < 1e-12:
            trial = _cross((0.0, 1.0, 0.0), z)
        x = trial
    x = _normalize(x)
    y = _cross(z, x)
    # Columns are X, Y, Z axes; last column is the origin.
    return [[x[0], y[0], z[0], o[0]],
            [x[1], y[1], z[1], o[1]],
            [x[2], y[2], z[2], o[2]],
            [0.0, 0.0, 0.0, 1.0]]


def _local_placement(placement) -> Mat4:
    """Compose an IfcLocalPlacement chain (RelativePlacement nested via PlacementRelTo) -> global 4x4."""
    if placement is None:
        return _identity()
    rel_to = getattr(placement, "PlacementRelTo", None)
    parent = _local_placement(rel_to) if rel_to is not None else _identity()
    rel = getattr(placement, "RelativePlacement", None)
    return _mat_mul(parent, _axis2placement3d(rel))


def _material_obj(el):
    try:
        return ue.get_material(el)
    except Exception:
        return None


def _section_name(el) -> str:
    mat = _material_obj(el)
    # IfcMaterialProfileSet / single profile -> profile name (e.g. "IPE300")
    profiles = getattr(mat, "MaterialProfiles", None)
    if profiles:
        prof = getattr(profiles[0], "Profile", None)
        name = getattr(prof, "ProfileName", None)
        if name:
            return str(name)
    return str(getattr(el, "ObjectType", None) or getattr(el, "Name", None) or "")


def _grade(el) -> str | None:
    mat = _material_obj(el)
    names = []
    if mat is not None:
        names.append(getattr(mat, "Name", "") or "")
        for p in getattr(mat, "MaterialProfiles", None) or []:
            names.append(getattr(getattr(p, "Material", None), "Name", "") or "")
    for n in names:
        m = _GRADE_RE.search(n)
        if m:
            return "S" + m.group(1)
    return None


def _profile_dims(el, scale_to_m: float) -> dict:
    """Measured section dimensions from an I-shaped material profile (IfcIShapeProfileDef).

    Returns whichever of ``{h_mm, b_mm, tf_mm, tw_mm}`` the profile carries (often all four), so the
    mapping layer can confirm a fuzzy/unknown profile *name* by physical dimensions. Profiles of
    other shapes simply lack these attributes and yield ``{}``.
    """
    mat = _material_obj(el)
    profiles = getattr(mat, "MaterialProfiles", None)
    prof = getattr(profiles[0], "Profile", None) if profiles else None
    if prof is None:
        return {}
    to_mm = scale_to_m * 1000.0
    dims = {}
    for attr, key in (("OverallDepth", "h_mm"), ("OverallWidth", "b_mm"),
                      ("FlangeThickness", "tf_mm"), ("WebThickness", "tw_mm")):
        v = getattr(prof, attr, None)
        if isinstance(v, (int, float)) and v > 0:
            dims[key] = float(v) * to_mm
    return dims


def _length_mm(el, scale_to_m: float) -> float:
    """Find a 'Length' base quantity (any case) and convert to mm."""
    qsets = ue.get_psets(el, qtos_only=True) or {}
    for qto in qsets.values():
        for key, val in qto.items():
            if key.lower() == "length" and isinstance(val, (int, float)):
                return float(val) * scale_to_m * 1000.0
    return 0.0


def _polyline_endpoints(poly) -> tuple[Vec3, Vec3] | None:
    """First and last points of an IfcPolyline (axis line), in the representation's local frame."""
    pts = getattr(poly, "Points", None)
    if not pts or len(pts) < 2:
        return None
    a = getattr(pts[0], "Coordinates", None)
    b = getattr(pts[-1], "Coordinates", None)
    if not a or not b:
        return None
    a3 = (float(a[0]), float(a[1]), float(a[2]) if len(a) > 2 else 0.0)
    b3 = (float(b[0]), float(b[1]), float(b[2]) if len(b) > 2 else 0.0)
    return a3, b3


def _trimmed_line_endpoints(curve) -> tuple[Vec3, Vec3] | None:
    """Endpoints of an IfcTrimmedCurve whose BasisCurve is an IfcLine, using cartesian trims.

    IfcLine = Pnt (origin) + Dir (IfcVector: Orientation direction * Magnitude). A cartesian trim
    gives the start/end points directly; a parametric trim gives distances along the line. We read
    cartesian trims first (the common, unambiguous case) and fall back to parameters.
    """
    basis = getattr(curve, "BasisCurve", None)
    if basis is None or not basis.is_a("IfcLine"):
        return None
    t1 = getattr(curve, "Trim1", None) or ()
    t2 = getattr(curve, "Trim2", None) or ()

    def _cartesian(trim):
        for item in trim:
            if item is not None and item.is_a("IfcCartesianPoint"):
                c = item.Coordinates
                return (float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0)
        return None

    p_start, p_end = _cartesian(t1), _cartesian(t2)
    if p_start is not None and p_end is not None:
        return p_start, p_end

    # Parametric fallback: point(u) = Pnt + u * Dir.Orientation * Dir.Magnitude.
    pnt = getattr(basis, "Pnt", None)
    vec = getattr(basis, "Dir", None)
    if pnt is None or vec is None:
        return None
    o = pnt.Coordinates
    o3 = (float(o[0]), float(o[1]), float(o[2]) if len(o) > 2 else 0.0)
    orient = vec.Orientation.DirectionRatios
    mag = float(getattr(vec, "Magnitude", 1.0) or 1.0)
    d = (float(orient[0]) * mag, float(orient[1]) * mag,
         (float(orient[2]) if len(orient) > 2 else 0.0) * mag)

    def _param(trim):
        for item in trim:
            if isinstance(item, (int, float)):
                return float(item)
        return None

    u1, u2 = _param(t1), _param(t2)
    if u1 is None or u2 is None:
        return None
    return ((o3[0] + u1 * d[0], o3[1] + u1 * d[1], o3[2] + u1 * d[2]),
            (o3[0] + u2 * d[0], o3[1] + u2 * d[1], o3[2] + u2 * d[2]))


def _indexed_polycurve_endpoints(curve) -> tuple[Vec3, Vec3] | None:
    """First/last points of an IfcIndexedPolyCurve (the IFC4 axis curve ifcopenshell emits)."""
    pts = getattr(curve, "Points", None)
    coords = getattr(pts, "CoordList", None)
    if not coords or len(coords) < 2:
        return None
    a, b = coords[0], coords[-1]
    a3 = (float(a[0]), float(a[1]), float(a[2]) if len(a) > 2 else 0.0)
    b3 = (float(b[0]), float(b[1]), float(b[2]) if len(b) > 2 else 0.0)
    return a3, b3


def _axis_local_endpoints(rep) -> tuple[Vec3, Vec3] | None:
    """Endpoints of an 'Axis' IfcShapeRepresentation in its local frame.

    Handles the three straight-line axis encodings seen in the wild: ``IfcPolyline`` (Revit/Tekla),
    ``IfcTrimmedCurve`` on an ``IfcLine``, and ``IfcIndexedPolyCurve`` (what ifcopenshell's own
    ``add_axis_representation`` writes for IFC4).
    """
    for item in getattr(rep, "Items", None) or []:
        if item.is_a("IfcPolyline"):
            ep = _polyline_endpoints(item)
        elif item.is_a("IfcTrimmedCurve"):
            ep = _trimmed_line_endpoints(item)
        elif item.is_a("IfcIndexedPolyCurve"):
            ep = _indexed_polycurve_endpoints(item)
        else:
            ep = None
        if ep:
            return ep
    return None


def _shape_reps(el):
    """All IfcShapeRepresentation entities directly under the product's body, by identifier."""
    rep = getattr(el, "Representation", None)
    out = []
    for shape_rep in getattr(rep, "Representations", None) or []:
        if shape_rep.is_a("IfcShapeRepresentation"):
            out.append(shape_rep)
    return out


def _extrusion_axis(rep) -> tuple[Vec3, Vec3] | None:
    """Axis of a swept solid: (base_origin, top) from the first IfcExtrudedAreaSolid in ``rep``.

    The extrusion's Position places the swept profile; ExtrudedDirection x Depth is the member axis
    (a point-placed column's vertical run, mirroring how Revit columns carry base/top). Coordinates
    are in the representation's local frame; the caller transforms them to global.
    """
    for item in getattr(rep, "Items", None) or []:
        if not item.is_a("IfcExtrudedAreaSolid"):
            continue
        pos = getattr(item, "Position", None)
        # Compose the solid's own placement so a non-trivial Position is honoured.
        base_local = _axis2placement3d(pos) if pos is not None else _identity()
        base = _mat_apply(base_local, (0.0, 0.0, 0.0))
        direction = getattr(item, "ExtrudedDirection", None)
        depth = float(getattr(item, "Depth", 0.0) or 0.0)
        if direction is None or depth <= 0:
            return None
        dr = direction.DirectionRatios
        d = _normalize((float(dr[0]), float(dr[1]), float(dr[2]) if len(dr) > 2 else 0.0))
        # Extrusion direction is expressed in the solid's placement frame -> rotate it there.
        d_world = _mat_apply_dir(base_local, d)
        top = (base[0] + d_world[0] * depth, base[1] + d_world[1] * depth,
               base[2] + d_world[2] * depth)
        return base, top
    return None


def _member_axis(el, scale_to_m: float) -> tuple[list[float] | None, list[float] | None, str]:
    """Resolve a member's axis endpoints in global coordinates (mm). Honest about failure.

    Strategy, cleanest first:
      1. an 'Axis' shape representation (IfcPolyline / trimmed IfcLine) — the true member centreline;
      2. otherwise a 'Body' swept solid (IfcExtrudedAreaSolid) — base + extrusion (vertical columns).
    Endpoints are taken in the representation's local frame, then transformed by the composed
    ObjectPlacement to the global frame and scaled to mm. If neither resolves, returns
    ``(None, None, note)`` so the member is left coordinate-free with a recorded reason
    (CLAUDE.md honesty rule: never guess an axis).
    """
    placement = _local_placement(getattr(el, "ObjectPlacement", None))
    to_mm = scale_to_m * 1000.0

    local = None
    for rep in _shape_reps(el):
        if (rep.RepresentationIdentifier or "") == "Axis":
            local = _axis_local_endpoints(rep)
            if local:
                break
    if local is None:
        for rep in _shape_reps(el):
            if (rep.RepresentationIdentifier or "") in ("Body", "SweptSolid", ""):
                local = _extrusion_axis(rep)
                if local:
                    break

    if local is None:
        return None, None, "no resolvable axis geometry (no Axis curve or extruded-solid body)"

    p0 = _mat_apply(placement, local[0])
    p1 = _mat_apply(placement, local[1])
    start = [p0[0] * to_mm, p0[1] * to_mm, p0[2] * to_mm]
    end = [p1[0] * to_mm, p1[1] * to_mm, p1[2] * to_mm]
    if _norm((end[0] - start[0], end[1] - start[1], end[2] - start[2])) < 1e-6:
        return None, None, "degenerate (zero-length) axis geometry"
    return start, end, ""


def _append_note(existing: str, note: str) -> str:
    if not note:
        return existing
    return f"{existing}; {note}" if existing else note


def extract_ifc(path: str, kind: str = "donor") -> ExtractedModel:
    model = ifcopenshell.open(path)
    scale_to_m = uu.calculate_unit_scale(model)  # model length unit -> metres
    members: list[ExtractedMember] = []
    for ifc_class, role in _CLASS_ROLE:
        for el in model.by_type(ifc_class):
            length = _length_mm(el, scale_to_m)
            start_xyz, end_xyz, note = _member_axis(el, scale_to_m)
            # If no base-quantity Length was found, fall back to the resolved axis length so the
            # member still carries a usable physical length (kept consistent with the geometry).
            if not length and start_xyz is not None and end_xyz is not None:
                length = _norm((end_xyz[0] - start_xyz[0], end_xyz[1] - start_xyz[1],
                                end_xyz[2] - start_xyz[2]))
            members.append(ExtractedMember(
                id=str(getattr(el, "GlobalId", None) or el.id()),
                role=role, category=ifc_class,
                raw_section=_section_name(el), material_grade=_grade(el),
                length_mm=length, spans_mm=[length] if length else [],
                start_xyz=start_xyz, end_xyz=end_xyz,
                notes=_append_note("", note),
                **_profile_dims(el, scale_to_m),
            ))
    name = ""
    projects = model.by_type("IfcProject")
    if projects:
        name = projects[0].Name or ""
    return ExtractedModel(kind=kind, members=members, source="ifc", model_name=name)
