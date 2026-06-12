# -*- coding: utf-8 -*-
"""Follow a reuse pairing from the selected element to its partner element(s) in the other model.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).
Stdlib only, no f-strings, %-formatting.

"Apply Matches" writes each matched element's partners into the "Reuse Paired With" shared
parameter: on a DONOR element the slot id(s) it fills (``<demand element id>#<span>``), on a DEMAND
element the donor element id(s) that fill it. So tracing works entirely off the model:

  1. Select a matched element (or pick one when nothing is selected) and run Trace Match.
  2. The partner element ids are parsed from "Reuse Paired With" (slot ids lose their ``#k``).
  3. Every open document is searched for those ids; the best match is the document that actually
     contains them (preferring one whose elements reference the selected element back).
  4. That document is activated (if it is not already) and the partner element(s) are selected
     and zoomed to.

Works in both directions; the paired model must be OPEN in the same Revit session (Revit cannot
activate a document it has not loaded -- when it is not open, the ids are printed instead).
"""

from pyrevit import DB, forms, revit, script
from System.Collections.Generic import List

output = script.get_output()
uiapp = __revit__                      # noqa: F821 -- provided by pyRevit at runtime
doc = revit.doc

PARAM_STATUS = "Reuse Status"
PARAM_PAIRED = "Reuse Paired With"

_STRUCT_CATS = (int(DB.BuiltInCategory.OST_StructuralFraming),
                int(DB.BuiltInCategory.OST_StructuralColumns))


def _idval(element_id):
    """ElementId's integer value across API generations (.Value is the 2024+ way)."""
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _param_str(elem, name):
    try:
        p = elem.LookupParameter(name)
        if p is not None and p.HasValue:
            return p.AsString() or ""
    except Exception:
        pass
    return ""


def _source_element():
    """The element to trace from: current selection, else an interactive pick."""
    sel = revit.get_selection()
    if len(sel) == 1:
        return sel.first
    if len(sel) > 1:
        forms.alert("Select a single matched element to trace.")
        return None
    try:
        return revit.pick_element("Pick a matched element to trace")
    except Exception:
        return None


def _partner_ids(paired_str):
    """Parse 'Reuse Paired With' into integer element ids (slot ids lose their '#k' suffix)."""
    ids = []
    for token in paired_str.split(","):
        token = token.strip().split("#")[0]
        if token.isdigit() and int(token) not in ids:
            ids.append(int(token))
    return ids


def _is_structural(elem):
    try:
        return elem.Category is not None and _idval(elem.Category.Id) in _STRUCT_CATS
    except Exception:
        return False


def _resolve_in(target_doc, ids):
    """The subset of ids that exist in ``target_doc`` as structural framing/column elements."""
    found = []
    for i in ids:
        try:
            elem = target_doc.GetElement(DB.ElementId(i))
        except Exception:
            elem = None
        if elem is not None and _is_structural(elem):
            found.append(elem)
    return found


def _backrefs(elems, source_id_str):
    """How many of ``elems`` reference the source element back in their own pairing parameter."""
    n = 0
    for e in elems:
        if source_id_str in _param_str(e, PARAM_PAIRED):
            n += 1
    return n


def _best_document(ids, source_id_str):
    """Open document holding the partner ids: most back-references wins, then most ids found.

    The active document participates too (a donor and demand extracted from one model still
    trace), but other documents win ties -- partners normally live in the other model.
    """
    best = None  # ((backrefs, hits, is_other_doc), doc, elements)
    for d in uiapp.Application.Documents:
        if d.IsLinked:
            continue
        elems = _resolve_in(d, ids)
        if not elems:
            continue
        score = (_backrefs(elems, source_id_str), len(elems),
                 0 if d.Equals(doc) else 1)
        if best is None or score > best[0]:
            best = (score, d, elems)
    return (best[1], best[2]) if best else (None, None)


def _select_and_zoom(target_doc, elems):
    """Activate ``target_doc`` if needed, then select + zoom to ``elems``. True on success."""
    eids = List[DB.ElementId]([e.Id for e in elems])
    if target_doc.Equals(doc):
        uidoc = revit.uidoc
    else:
        path = target_doc.PathName
        if not path:
            return False  # never saved -> Revit cannot activate it by path
        try:
            uidoc = uiapp.OpenAndActivateDocument(path)
        except Exception:
            return False
    try:
        uidoc.Selection.SetElementIds(eids)
        uidoc.ShowElements(eids)
    except Exception:
        return False
    return True


def main():
    elem = _source_element()
    if elem is None:
        return

    status = _param_str(elem, PARAM_STATUS)
    paired = _param_str(elem, PARAM_PAIRED)
    if not paired:
        forms.alert("No reuse pairing on this element.\n\n"
                    "Run Apply Matches first; only reused/filled elements carry a partner.")
        return
    ids = _partner_ids(paired)
    if not ids:
        forms.alert("Could not read partner element ids from '%s' = '%s'."
                    % (PARAM_PAIRED, paired))
        return

    source_id_str = str(_idval(elem.Id))
    id_list = ", ".join(str(i) for i in ids)
    direction = ("donor -> used in the new design" if status == "reused"
                 else "demand -> filled by donor member(s)")
    output.print_md("**Trace Match** (%s): element %s -> partner id(s) %s"
                    % (direction, source_id_str, id_list))

    target_doc, elems = _best_document(ids, source_id_str)
    if target_doc is None:
        forms.alert("Partner element(s) %s were not found in any open document.\n\n"
                    "Open the paired model in this Revit session and run Trace Match again."
                    % id_list)
        return

    if len(elems) < len(ids):
        found_ids = [_idval(e.Id) for e in elems]
        missing = ", ".join(str(i) for i in ids if i not in found_ids)
        output.print_md("(id(s) %s not found in '%s')" % (missing, target_doc.Title))
    if _select_and_zoom(target_doc, elems):
        output.print_md("Selected + zoomed to **%d** element(s) in **%s**."
                        % (len(elems), target_doc.Title))
    else:
        forms.alert("Found the partner(s) in '%s' but could not activate it (unsaved document?).\n"
                    "Switch to it manually and look up element id(s): %s"
                    % (target_doc.Title, id_list))


if __name__ == "__main__":
    main()
