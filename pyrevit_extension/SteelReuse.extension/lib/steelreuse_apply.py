# -*- coding: utf-8 -*-
"""Shared Apply-Matches logic: colour a model's elements by reuse status + write the schedulable
reuse-passport parameters, from a status.json (:func:`steelreuse.writeback.build_writeback`).

Used by BOTH the Apply Matches ribbon button (modal command context) and the SteelReuse window's
Apply button (via an ExternalEvent). Every function takes ``doc`` explicitly so it is valid in either
context, and :func:`apply_matches` opens/commits its own transaction. IronPython 3, stdlib + Revit
API, no f-strings.
"""

import os

from pyrevit import DB

# Statuses worth a clickable element list after applying (the ribbon button linkifies these).
ATTENTION_STATUSES = {
    "donor": ("quarantined",),
    "demand": ("partially_filled", "unfilled"),
}

PARAM_STATUS = "Reuse Status"
PARAM_NOTE = "Reuse Note"
PARAM_PAIRED = "Reuse Paired With"
PARAM_CO2 = "Reuse CO2 Saved (kg)"
SHARED_PARAMS = ((PARAM_STATUS, "text"), (PARAM_NOTE, "text"),
                 (PARAM_PAIRED, "text"), (PARAM_CO2, "number"))
SP_GROUP = "SteelReuse"
# lib/ -> SteelReuse.extension -> pyrevit_extension (where the persistent shared-param file lives).
DEFAULT_SP_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "steelreuse_shared_params.txt"))


def _solid_fill_pattern_id(doc):
    """First solid-fill drafting FillPattern in ``doc``, or None if it has none (rare)."""
    for fp in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
        if fp.GetFillPattern().IsSolidFill:
            return fp.Id
    return None


def _overrides(color_rgb, solid_fill_id):
    """OverrideGraphicSettings for one status colour, or None for "leave element as-is"."""
    if color_rgb is None:
        return None
    color = DB.Color(color_rgb[0], color_rgb[1], color_rgb[2])
    ogs = DB.OverrideGraphicSettings()
    ogs.SetProjectionLineColor(color)
    ogs.SetCutLineColor(color)
    if solid_fill_id is not None:
        ogs.SetSurfaceForegroundPatternId(solid_fill_id)
        ogs.SetSurfaceForegroundPatternColor(color)
        ogs.SetCutForegroundPatternId(solid_fill_id)
        ogs.SetCutForegroundPatternColor(color)
    return ogs


def _spec(kind):
    """Parameter data type, new API (SpecTypeId, Revit 2022+) with legacy ParameterType fallback."""
    spec = getattr(DB, "SpecTypeId", None)
    if spec is not None:
        return spec.String.Text if kind == "text" else spec.Number
    pt = DB.ParameterType
    return pt.Text if kind == "text" else pt.Number


def _insert_binding(doc, defn, binding):
    """Bind a definition under the Data group; tolerate older Revit overloads."""
    group_id = getattr(DB, "GroupTypeId", None)
    if group_id is not None:
        try:
            doc.ParameterBindings.Insert(defn, binding, group_id.Data)
            return
        except Exception:  # noqa: BLE001 -- fall through to the legacy overload
            pass
    doc.ParameterBindings.Insert(defn, binding)


def _ensure_shared_params(doc, sp_file):
    """Create + bind the SteelReuse instance parameters on framing/columns (no-op when already bound).

    Must run inside an open transaction. Restores the user's shared-parameter-file setting afterwards.
    """
    bound = set()
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        bound.add(it.Key.Name)
    missing = [(n, k) for n, k in SHARED_PARAMS if n not in bound]
    if not missing:
        return

    app = doc.Application
    if not os.path.exists(sp_file):
        open(sp_file, "a").close()
    original_spf = app.SharedParametersFilename
    app.SharedParametersFilename = sp_file
    try:
        sp = app.OpenSharedParameterFile()
        group = None
        for g in sp.Groups:
            if g.Name == SP_GROUP:
                group = g
        if group is None:
            group = sp.Groups.Create(SP_GROUP)

        cats = app.Create.NewCategorySet()
        for bic in (DB.BuiltInCategory.OST_StructuralFraming,
                    DB.BuiltInCategory.OST_StructuralColumns):
            cats.Insert(doc.Settings.Categories.get_Item(bic))
        binding = app.Create.NewInstanceBinding(cats)

        for name, kind in missing:
            defn = None
            for d in group.Definitions:
                if d.Name == name:
                    defn = d
            if defn is None:
                defn = group.Definitions.Create(
                    DB.ExternalDefinitionCreationOptions(name, _spec(kind)))
            _insert_binding(doc, defn, binding)
    finally:
        try:
            if original_spf:
                app.SharedParametersFilename = original_spf
        except Exception:  # noqa: BLE001
            pass


def _set_param(elem, name, value):
    p = elem.LookupParameter(name)
    if p is not None and not p.IsReadOnly:
        p.Set(value)


def apply_matches(doc, view, statuses, side, sp_file=None):
    """Colour ``doc``'s elements + write the reuse-passport params for one side, in a transaction.

    ``statuses`` is the per-element map from status.json (``data[side]``); ``side`` is
    ``'donor'``/``'demand'``. Returns ``{applied, missing, by_status, attention:{status:[ElementId]}}``
    for the caller to print. Opens/commits its own transaction, so it is valid from both a ribbon
    command and an ExternalEvent handler.
    """
    if sp_file is None:
        sp_file = DEFAULT_SP_FILE
    solid_fill_id = _solid_fill_pattern_id(doc)
    attention = dict((s, []) for s in ATTENTION_STATUSES.get(side, ()))
    applied, missing, by_status = 0, 0, {}

    t = DB.Transaction(doc, "SteelReuse: Apply Matches (%s)" % side)
    t.Start()
    try:
        _ensure_shared_params(doc, sp_file)
        for elem_id_str, info in statuses.items():
            try:
                eid = DB.ElementId(int(elem_id_str))
            except Exception:  # noqa: BLE001 -- a non-numeric id is just "not in this model"
                missing += 1
                continue
            elem = doc.GetElement(eid)
            if elem is None:
                missing += 1
                continue

            ogs = _overrides(info.get("color"), solid_fill_id)
            if ogs is not None:
                view.SetElementOverrides(eid, ogs)

            status = info.get("status", "")
            _set_param(elem, PARAM_STATUS, status)
            _set_param(elem, PARAM_NOTE, info.get("note", ""))
            _set_param(elem, PARAM_PAIRED, info.get("paired_with", "") or "")
            co2 = info.get("co2_saved_kg")
            if co2 is not None:
                _set_param(elem, PARAM_CO2, float(co2))

            applied += 1
            by_status[status] = by_status.get(status, 0) + 1
            if status in attention:
                attention[status].append(eid)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return {"applied": applied, "missing": missing, "by_status": by_status, "attention": attention}
