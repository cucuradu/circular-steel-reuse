# -*- coding: utf-8 -*-
"""Import a filled survey file and write its audit onto the matching elements.

Default IronPython 3 engine, stdlib + Revit/pyRevit only, no f-strings. The heavy parsing runs in the
CPython engine (steelreuse.survey) via a subprocess that emits a normalised JSON map; this button then
resolves elements and writes shared params with steelreuse_apply.write_pda.
"""

import json
import os
import subprocess

import steelreuse_apply as apply
import steelreuse_buttons as buttons
from pyrevit import forms, revit, script

output = script.get_output()
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _parse_survey(interp, survey_path):
    """Shell out to the engine to parse+normalise the survey into {key: {field: value}} JSON."""
    code = ("import json,sys;from steelreuse.survey import load_survey;"
            "sys.stdout.write(json.dumps(load_survey(sys.argv[1])))")
    proc = subprocess.Popen([interp, "-c", code, survey_path],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err or "survey parse failed")
    return json.loads(out)


def _resolve(doc, key):
    """Element for a survey key: UniqueId string first, then integer ElementId."""
    try:
        elem = doc.GetElement(key)        # works for a UniqueId string (raises on a malformed one)
    except Exception:  # noqa: BLE001 -- not a valid UniqueId; fall through to the integer-id path
        elem = None
    if elem is not None:
        return elem.Id
    try:
        return revit.DB.ElementId(int(key))
    except Exception:  # noqa: BLE001 -- not an integer id either -> unresolved
        return None


def main():
    doc = revit.doc
    interp = buttons.resolve_interpreter(_EXT_ROOT)
    if not interp:
        return  # resolve_interpreter already alerted
    path = forms.pick_file(file_ext="csv|xlsx|json", title="Pick a filled survey file")
    if not path:
        return
    try:
        records = _parse_survey(interp, path)
    except Exception as ex:  # noqa: BLE001
        forms.alert("Could not parse survey:\n\n%s" % ex, title="SteelReuse")
        return

    by_eid = {}
    missing = 0
    for key, values in records.items():
        eid = _resolve(doc, key)
        if eid is None:
            missing += 1
            continue
        by_eid[eid] = values

    written = 0
    for eid, values in by_eid.items():
        res = apply.write_pda(doc, [eid], values)
        written += res["written"]
    output.print_md("Imported audit to **%d** elements (%d rows matched no element). Re-extract to "
                    "feed the match." % (written, missing))


if __name__ == "__main__":
    main()
