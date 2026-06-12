# -*- coding: utf-8 -*-
"""Colour the active model's elements by their reuse status from a SteelReuse match run.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).
Stdlib only (json, os), no f-strings, %-formatting.

Reads the per-element status JSON written by ``steelreuse --apply-matches-out status.json``
(:func:`steelreuse.writeback.build_writeback`):
``{"donor": {element_id: {status, color, note, paired_with, co2_saved_kg}}, "demand": {...},
"summary": {...}}``.

What it writes per element:
  * a solid-colour graphic override in the ACTIVE view (green = reused/filled, amber = partially
    filled, red = quarantined, grey = available/unmapped, orange = unfilled);
  * the SteelReuse SHARED PARAMETERS (created + bound to framing/columns on first run, so they
    are schedulable): "Reuse Status", "Reuse Note", "Reuse Paired With", "Reuse CO2 Saved (kg)".
    The model itself then carries the reuse passport -- "Comments" is left alone.

Usage in Revit:
  1. Open the SAME model the donor or demand JSON was extracted from (element ids must match).
  2. SteelReuse tab -> Apply Matches.
  3. Pick the status JSON.
  4. Choose whether THIS model is the donor or the demand side.
"""

import json
import os

from pyrevit import DB, forms, revit, script

output = script.get_output()
doc = revit.doc

# Statuses worth a clickable element list after applying (click = select + zoom in Revit).
ATTENTION_STATUSES = {
    "donor": ("quarantined",),
    "demand": ("partially_filled", "unfilled"),
}
MAX_LINKS = 25  # cap the clickable list per status so huge models stay readable

# Schedulable instance parameters bound to Structural Framing + Structural Columns. Definitions
# live in a persistent shared-parameter file inside the extension folder, so the GUIDs stay stable
# across models and re-runs (re-binding never duplicates them).
PARAM_STATUS = "Reuse Status"
PARAM_NOTE = "Reuse Note"
PARAM_PAIRED = "Reuse Paired With"
PARAM_CO2 = "Reuse CO2 Saved (kg)"
SHARED_PARAMS = ((PARAM_STATUS, "text"), (PARAM_NOTE, "text"),
                 (PARAM_PAIRED, "text"), (PARAM_CO2, "number"))
SP_GROUP = "SteelReuse"
# .../ApplyMatches.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension -> pyrevit_extension
_EXT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
SP_FILE = os.path.join(_EXT_DIR, "steelreuse_shared_params.txt")


def _solid_fill_pattern_id():
    """First solid-fill drafting FillPattern, or None if the model has none (rare)."""
    for fp in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
        pattern = fp.GetFillPattern()
        if pattern.IsSolidFill:
            return fp.Id
    return None


def _overrides(color_rgb, solid_fill_id):
    """OverrideGraphicSettings for one status, or None for "leave element as-is"."""
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
    """Parameter data type, new API (SpecTypeId, Revit 2022+) with legacy fallback."""
    spec = getattr(DB, "SpecTypeId", None)
    if spec is not None:
        return spec.String.Text if kind == "text" else spec.Number
    pt = DB.ParameterType
    return pt.Text if kind == "text" else pt.Number


def _insert_binding(defn, binding):
    """Bind under the Data group; tolerate older Revit overloads."""
    group_id = getattr(DB, "GroupTypeId", None)
    if group_id is not None:
        try:
            doc.ParameterBindings.Insert(defn, binding, group_id.Data)
            return
        except Exception:
            pass
    doc.ParameterBindings.Insert(defn, binding)


def _ensure_shared_params():
    """Create + bind the SteelReuse instance parameters (no-op when already bound).

    Must be called inside an open transaction. The user's shared-parameter file setting is
    restored afterwards.
    """
    bound = set()
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        bound.add(it.Key.Name)
    missing = [(n, k) for n, k in SHARED_PARAMS if n not in bound]
    if not missing:
        return

    app = doc.Application
    if not os.path.exists(SP_FILE):
        open(SP_FILE, "a").close()
    original_spf = app.SharedParametersFilename
    app.SharedParametersFilename = SP_FILE
    try:
        sp_file = app.OpenSharedParameterFile()
        group = None
        for g in sp_file.Groups:
            if g.Name == SP_GROUP:
                group = g
        if group is None:
            group = sp_file.Groups.Create(SP_GROUP)

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
            _insert_binding(defn, binding)
    finally:
        try:
            if original_spf:
                app.SharedParametersFilename = original_spf
        except Exception:
            pass


def _set_param(elem, name, value):
    p = elem.LookupParameter(name)
    if p is not None and not p.IsReadOnly:
        p.Set(value)


def main():
    json_path = forms.pick_file(file_ext="json", title="Pick the SteelReuse apply-matches JSON")
    if not json_path:
        return
    with open(json_path) as fh:
        data = json.load(fh)

    side = forms.CommandSwitchWindow.show(
        ["donor", "demand"],
        message="Is THIS open model the DONOR (reclaimed supply) or the DEMAND (new design)?",
    )
    if not side:
        return
    statuses = data.get(side, {})
    if not statuses:
        forms.alert("No '%s' entries in this JSON." % side)
        return

    view = doc.ActiveView
    solid_fill_id = _solid_fill_pattern_id()

    attention = dict((s, []) for s in ATTENTION_STATUSES.get(side, ()))
    applied, missing, by_status = 0, 0, {}
    with revit.Transaction("SteelReuse: Apply Matches (%s)" % side):
        _ensure_shared_params()
        for elem_id_str, info in statuses.items():
            try:
                eid = DB.ElementId(int(elem_id_str))
            except Exception:
                missing += 1
                continue
            elem = doc.GetElement(eid)
            if elem is None:
                missing += 1
                continue

            status = info.get("status", "")
            ogs = _overrides(info.get("color"), solid_fill_id)
            if ogs is not None:
                view.SetElementOverrides(eid, ogs)

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

    # Headline numbers (computed by the external pipeline; just printed here).
    summary = data.get("summary") or {}
    if summary:
        output.print_md("**Run summary**: %s of %s slot(s) filled by reuse | %.0f kg CO2e saved "
                        "| %s donor member(s) in stock."
                        % (summary.get("n_reused", "?"), summary.get("slot_count", "?"),
                           summary.get("co2_saved_kg", 0.0), summary.get("supply_count", "?")))

    output.print_md("**Applied %d %s element(s)** in view '%s' (%d id(s) not found in this model)."
                     % (applied, side, view.Name, missing))
    for status, count in sorted(by_status.items()):
        output.print_md("- %s: %d" % (status, count))

    # Clickable lists for the statuses that need a human decision (click = select + zoom).
    for status in ATTENTION_STATUSES.get(side, ()):
        ids = attention.get(status, [])
        if not ids:
            continue
        shown = ids[:MAX_LINKS]
        links = " ".join(output.linkify(i) for i in shown)
        more = " ... +%d more" % (len(ids) - len(shown)) if len(ids) > len(shown) else ""
        output.print_md("**%s** (%d): %s%s" % (status, len(ids), links, more))

    output.print_md("Reuse data written to the '%s' parameters -- use **Reuse Schedule** to see "
                    "the passport as a native Revit schedule." % SP_GROUP)


if __name__ == "__main__":
    main()
