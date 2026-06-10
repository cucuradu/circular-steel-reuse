"""IFC ingestion (IfcOpenShell) — a Revit-free path to the same JSON schema as the pyRevit extractor.

Reads IfcBeam / IfcColumn / IfcMember elements and pulls:
  * raw_section — from the material profile name, else ObjectType, else Name;
  * material_grade — parsed (S235/S275/...) from the associated material name;
  * length_mm — from a base-quantity "Length", converted to mm via the file's unit scale.

Unlike the pyRevit extractor (which runs inside Revit), this runs in the normal CPython environment,
so the whole pipeline can be exercised on real BIM data without Revit.
"""

from __future__ import annotations

import re

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uu

from .schema import ExtractedMember, ExtractedModel

_GRADE_RE = re.compile(r"S\s*(235|275|355|420|460)", re.IGNORECASE)
_CLASS_ROLE = (("IfcBeam", "beam"), ("IfcColumn", "column"), ("IfcMember", "brace"))


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


def extract_ifc(path: str, kind: str = "donor") -> ExtractedModel:
    model = ifcopenshell.open(path)
    scale_to_m = uu.calculate_unit_scale(model)  # model length unit -> metres
    members: list[ExtractedMember] = []
    for ifc_class, role in _CLASS_ROLE:
        for el in model.by_type(ifc_class):
            length = _length_mm(el, scale_to_m)
            members.append(ExtractedMember(
                id=str(getattr(el, "GlobalId", None) or el.id()),
                role=role, category=ifc_class,
                raw_section=_section_name(el), material_grade=_grade(el),
                length_mm=length, spans_mm=[length] if length else [],
                **_profile_dims(el, scale_to_m),
            ))
    name = ""
    projects = model.by_type("IfcProject")
    if projects:
        name = projects[0].Name or ""
    return ExtractedModel(kind=kind, members=members, source="ifc", model_name=name)
