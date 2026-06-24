# -*- coding: utf-8 -*-
"""Shared Revit ``ExternalEvent`` handlers for the SteelReuse modeless windows (Run Match, Results,
Compare Runs).

A modeless WPF window may not touch the Revit API directly -- document actions (select/zoom, open a
model, run a colouring transaction) must run inside a valid API context, which Revit only gives us
when it raises an ``ExternalEvent``. These three handlers are that bridge; every window stages its
inputs on the handler instance and calls ``event.Raise()``.

IronPython-only (.NET + pyRevit), so it is not unit-tested headlessly -- same as steelreuse_panel.py.
Stdlib + .NET only, no f-strings, %-formatting.
"""

import steelreuse_apply as apply_mod  # shared Apply-Matches logic (also used by the ribbon button)
from Autodesk.Revit.DB import BuiltInCategory, ElementId
from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler
from pyrevit import forms
from System.Collections.Generic import List

_STRUCT_CATS = (int(BuiltInCategory.OST_StructuralFraming),
                int(BuiltInCategory.OST_StructuralColumns))


def _to_element_ids(raw_ids):
    """The parseable subset of ``raw_ids`` (ints or numeric strings) as .NET ``ElementId``s."""
    out = []
    for raw in raw_ids:
        try:
            out.append(ElementId(int(raw)))
        except (ValueError, TypeError):
            continue  # a non-numeric id (e.g. an IFC GUID) cannot address a Revit element
    return out


def _select_and_zoom(uidoc, element_ids):
    """Select + zoom ``element_ids`` (a list of ElementId) that exist in ``uidoc``'s document. True if
    any were shown."""
    doc = uidoc.Document
    found = List[ElementId]()
    for eid in element_ids:
        try:
            if doc.GetElement(eid) is not None:
                found.Add(eid)
        except Exception:  # noqa: BLE001 -- a stale id must never break the handler
            pass
    if found.Count == 0:
        return False
    try:
        uidoc.Selection.SetElementIds(found)
        uidoc.ShowElements(found)
        return True
    except Exception:  # noqa: BLE001
        return False


class ZoomHandler(IExternalEventHandler):
    """Select + zoom the staged ``ids`` in the ACTIVE document.

    ``ids`` holds a row's candidate element ids (its demand and donor ids); whichever exists in the
    open model is selected, so the same Zoom works whether the donor or the demand model is active.
    """

    def __init__(self):
        self.ids = []

    def Execute(self, uiapp):
        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            return
        _select_and_zoom(uidoc, _to_element_ids(self.ids))

    def GetName(self):
        return "SteelReuse: zoom to element"


class TraceHandler(IExternalEventHandler):
    """Follow a match to its partner: select + zoom the staged ``ids`` in the best OTHER open model.

    The active model holds one side of a pairing (say the demand member); its donor lives in the
    *other* extracted model. Given both candidate ids, this finds the open, non-linked document --
    preferring one that is NOT the active document -- that actually contains them, activates it, and
    zooms. If only the active document matches (the paired model is not open), it says so instead of
    silently doing nothing.
    """

    def __init__(self):
        self.ids = []

    def _is_structural(self, elem):
        try:
            return elem.Category is not None and int(elem.Category.Id.Value) in _STRUCT_CATS
        except Exception:  # noqa: BLE001 -- .Value is 2024+; fall back below
            pass
        try:
            return elem.Category is not None and int(elem.Category.Id.IntegerValue) in _STRUCT_CATS
        except Exception:  # noqa: BLE001
            return False

    def _hits_in(self, doc, element_ids):
        """The structural elements among ``element_ids`` that exist in ``doc`` (a framing/column)."""
        found = []
        for eid in element_ids:
            try:
                elem = doc.GetElement(eid)
            except Exception:  # noqa: BLE001
                elem = None
            if elem is not None and self._is_structural(elem):
                found.append(elem)
        return found

    def Execute(self, uiapp):
        element_ids = _to_element_ids(self.ids)
        if not element_ids:
            return
        active = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
        best = None  # ((is_other_doc, hits), doc, elements)
        for doc in uiapp.Application.Documents:
            if doc.IsLinked:
                continue
            elems = self._hits_in(doc, element_ids)
            if not elems:
                continue
            is_other = 0 if (active is not None and doc.Equals(active)) else 1
            score = (is_other, len(elems))
            if best is None or score > best[0]:
                best = (score, doc, elems)
        if best is None:
            forms.alert("The paired element was not found in any open model.\n\nOpen the other "
                        "extracted model (donor or demand) in this Revit session, then trace again.",
                        title="SteelReuse: Trace")
            return
        score, doc, elems = best
        if score[0] == 0:
            forms.alert("Only the active model holds this element. Open the PAIRED model (the other "
                        "side of the match) to jump to its partner.", title="SteelReuse: Trace")
            return
        eids = List[ElementId]([e.Id for e in elems])
        path = doc.PathName
        if not path:
            forms.alert("The paired model has not been saved, so Revit cannot activate it by path.\n"
                        "Switch to it manually.", title="SteelReuse: Trace")
            return
        try:
            uidoc = uiapp.OpenAndActivateDocument(path)
            uidoc.Selection.SetElementIds(eids)
            uidoc.ShowElements(eids)
        except Exception as ex:  # noqa: BLE001 -- surface, never crash Revit
            forms.alert("Found the partner but could not activate its model:\n" + str(ex),
                        title="SteelReuse: Trace")

    def GetName(self):
        return "SteelReuse: trace to partner"


class ApplyHandler(IExternalEventHandler):
    """Apply the colour overrides + reuse-passport params to the active model, off an ExternalEvent.

    The window stages ``statuses`` (the donor/demand per-element status block) and ``side`` and raises
    this. Reuses the shared ``steelreuse_apply`` code the ribbon button uses, then reports the outcome.
    """

    def __init__(self):
        self.statuses = None
        self.side = None

    def Execute(self, uiapp):
        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            return
        doc = uidoc.Document
        try:
            result = apply_mod.apply_matches(doc, doc.ActiveView, self.statuses, self.side)
        except Exception as ex:  # noqa: BLE001 -- surface a failed apply, never crash Revit
            forms.alert("Apply Matches failed:\n" + str(ex), title="SteelReuse")
            return
        forms.alert("Applied %s %s element(s) in '%s'.\n%s id(s) were not in this model "
                    "(open the other side's model to colour those)."
                    % (result["applied"], self.side, doc.ActiveView.Name, result["missing"]),
                    title="SteelReuse: Apply Matches")

    def GetName(self):
        return "SteelReuse: apply matches"


def make_event(handler):
    """``ExternalEvent.Create(handler)`` -- a tiny alias so windows do not import the UI namespace."""
    return ExternalEvent.Create(handler)
