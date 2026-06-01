# -*- coding: utf-8 -*-
"""pyRevit extractor — runs INSIDE Revit on the default IronPython 3 engine.

NOTE on engine choice: there is intentionally NO ``#! python3`` shebang. pyRevit 6.x's bundled
CPython 3.12 engine has a version-parsing bug under Revit 2026 ("input string '3.12.3' was not in a
correct format"), so we run on the stable IronPython 3 engine instead. This script is therefore kept
strictly IronPython-safe: stdlib only (json, re), no f-strings, %-formatting, no third-party libs.

Reads structural steel framing + columns from the active document and writes a JSON file matching
``steelreuse.schema``. All heavy analysis happens later in the external CPython pipeline.

Usage in Revit: open the model, run via pyRevit, pick whether it is a DONOR (supply) or DEMAND
(new design) model and where to save the JSON.

Two geometry conventions (see plan, "Continuous-beam handling"):
  * DONOR  -> ``length_mm`` is the physical reusable stock length; ``spans_mm = [length_mm]``.
  * DEMAND -> the physical element is split at supports into ``spans_mm`` (structural spans).
"""

import json
import re

from pyrevit import DB, forms, revit, script

FT_TO_MM = 304.8
SUPPORT_TOL_MM = 50.0          # how close a support point must be to the beam line to count as a split
_GRADE_RE = re.compile(r"S\s*(235|275|355|420|460)", re.IGNORECASE)

output = script.get_output()
doc = revit.doc


def _mm(length_ft):
    return float(length_ft) * FT_TO_MM


def _id_str(element_id):
    """Stable element-id string across Revit versions.

    Revit 2024 deprecated ``ElementId.IntegerValue`` and it was removed in Revit 2026 in favour of
    ``ElementId.Value`` (a 64-bit long). Prefer ``.Value`` and fall back for older Revit.
    """
    val = getattr(element_id, "Value", None)
    if val is None:
        val = element_id.IntegerValue
    return str(val)


def _type_name(elem):
    """Best raw section name: '<Family> <Type>' (the mapping layer normalizes this later)."""
    try:
        sym = doc.GetElement(elem.GetTypeId())
        fam = getattr(getattr(sym, "Family", None), "Name", "") or ""
        typ = sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        typ = typ.AsString() if typ else getattr(sym, "Name", "")
        return (fam + " " + (typ or "")).strip()
    except Exception:
        return ""


def _grade(elem):
    """Try to read a steel grade (S235/S275/...) from the structural material name."""
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.STRUCTURAL_MATERIAL_PARAM)
        mat = doc.GetElement(p.AsElementId()) if p else None
        m = _GRADE_RE.search(mat.Name) if mat else None
        return ("S" + m.group(1)) if m else None
    except Exception:
        return None


def _level_name(elem):
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.FAMILY_LEVEL_PARAM)
        lvl = doc.GetElement(p.AsElementId()) if p else None
        return lvl.Name if lvl else None
    except Exception:
        return None


def _endpoints(elem):
    """Return (start_xyz_mm, end_xyz_mm, curve) for a location-curve element, else (None, None, None)."""
    try:
        loc = elem.Location
        crv = loc.Curve
        p0, p1 = crv.GetEndPoint(0), crv.GetEndPoint(1)
        return ([_mm(p0.X), _mm(p0.Y), _mm(p0.Z)],
                [_mm(p1.X), _mm(p1.Y), _mm(p1.Z)], crv)
    except Exception:
        return None, None, None


def _column_length_mm(elem):
    """Length of a (point-placed) structural column, in mm.

    Columns have a LocationPoint, not a curve, so we can't use ``curve.Length``. Try, in order:
      1. the built-in INSTANCE_LENGTH_PARAM (correct for slanted/curve-driven columns);
      2. top-level+offset minus base-level+offset (the usual vertical case);
      3. the element bounding-box height as a last resort.
    All Revit lengths are feet -> mm. Returns 0.0 only if nothing is readable.
    """
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.INSTANCE_LENGTH_PARAM)
        if p and p.HasValue and p.AsDouble() > 0:
            return _mm(p.AsDouble())
    except Exception:
        pass

    try:
        base_p = elem.get_Parameter(DB.BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)
        top_p = elem.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)
        base_lvl = doc.GetElement(base_p.AsElementId()) if base_p else None
        top_lvl = doc.GetElement(top_p.AsElementId()) if top_p else None
        if base_lvl is not None and top_lvl is not None:
            base_off = elem.get_Parameter(DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM)
            top_off = elem.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM)
            base = base_lvl.Elevation + (base_off.AsDouble() if base_off else 0.0)
            top = top_lvl.Elevation + (top_off.AsDouble() if top_off else 0.0)
            if abs(top - base) > 1e-6:
                return _mm(abs(top - base))
    except Exception:
        pass

    try:
        bb = elem.get_BoundingBox(None)
        if bb is not None:
            return _mm(abs(bb.Max.Z - bb.Min.Z))
    except Exception:
        pass
    return 0.0


def _collect(category):
    return (DB.FilteredElementCollector(doc)
            .OfCategory(category)
            .WhereElementIsNotElementType()
            .ToElements())


def _support_points(columns, beams):
    """Candidate support locations: column points + all beam endpoints (XYZ in feet)."""
    pts = []
    for c in columns:
        try:
            loc = c.Location
            if isinstance(loc, DB.LocationPoint):
                pts.append(loc.Point)
            else:
                pts.append(loc.Curve.GetEndPoint(0))
        except Exception:
            pass
    for b in beams:
        try:
            crv = b.Location.Curve
            pts.append(crv.GetEndPoint(0))
            pts.append(crv.GetEndPoint(1))
        except Exception:
            pass
    return pts


def _split_spans_mm(curve, support_pts):
    """Split a beam curve at interior support points -> list of span lengths in mm.

    Conservative + defensive: any failure falls back to a single span (full length).
    """
    try:
        total_mm = _mm(curve.Length)
        params = [0.0, 1.0]
        tol_ft = SUPPORT_TOL_MM / FT_TO_MM
        for p in support_pts:
            try:
                ir = curve.Project(p)
                if ir and ir.Distance <= tol_ft:
                    t = curve.ComputeNormalizedParameter(ir.Parameter)
                    if 0.01 < t < 0.99:
                        params.append(t)
            except Exception:
                continue
        params = sorted(set(round(t, 4) for t in params))
        spans = [(params[i + 1] - params[i]) * total_mm for i in range(len(params) - 1)]
        return [s for s in spans if s > 1.0] or [total_mm]
    except Exception:
        return [_mm(curve.Length)]


def extract(kind):
    beams = _collect(DB.BuiltInCategory.OST_StructuralFraming)
    columns = _collect(DB.BuiltInCategory.OST_StructuralColumns)
    support_pts = _support_points(columns, beams) if kind == "demand" else []

    members = []

    for col in columns:
        s, e, crv = _endpoints(col)
        # Most columns are point-placed (no curve); use a column-specific length helper.
        length = _mm(crv.Length) if crv else _column_length_mm(col)
        members.append({
            "id": _id_str(col.Id), "role": "column",
            "category": "Structural Columns", "raw_section": _type_name(col),
            "section": None, "material_grade": _grade(col), "level": _level_name(col),
            "length_mm": length, "spans_mm": [length] if length else [],
            "start_xyz": s, "end_xyz": e, "notes": "",
        })

    for bm in beams:
        s, e, crv = _endpoints(bm)
        length = _mm(crv.Length) if crv else 0.0
        if kind == "demand" and crv is not None:
            spans = _split_spans_mm(crv, support_pts)
            note = "split into %d span(s) at supports" % len(spans) if len(spans) > 1 else ""
        else:
            spans = [length] if length else []
            note = "donor: physical stock length" if kind == "donor" else ""
        members.append({
            "id": _id_str(bm.Id), "role": "beam",
            "category": "Structural Framing", "raw_section": _type_name(bm),
            "section": None, "material_grade": _grade(bm), "level": _level_name(bm),
            "length_mm": length, "spans_mm": spans,
            "start_xyz": s, "end_xyz": e, "notes": note,
        })

    return {
        "kind": kind, "members": members, "source": "pyrevit",
        "units": "extract:mm | internal:N,mm", "schema_version": 1,
        "model_name": doc.Title,
    }


def main():
    kind = forms.CommandSwitchWindow.show(
        ["donor", "demand"],
        message="Is this model the DONOR (reclaimed supply) or the new DEMAND design?",
    )
    if not kind:
        return
    payload = extract(kind)
    path = forms.save_file(file_ext="json", default_name="%s" % kind)
    if not path:
        return
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    output.print_md("**Extracted %d members** (%s) -> `%s`" % (len(payload["members"]), kind, path))


if __name__ == "__main__":
    main()
