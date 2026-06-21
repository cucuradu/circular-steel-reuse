# -*- coding: utf-8 -*-
"""Remove the SteelReuse problem-highlight overrides set by Highlight Problems."""

import os

import steelreuse_apply as apply  # noqa: E402
import steelreuse_runner as runner  # noqa: E402
from pyrevit import DB, revit, script

output = script.get_output()
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    doc = revit.doc
    ids = runner.load_settings(_EXT_ROOT).get("highlighted_ids", [])
    eids = []
    for s in ids:
        try:
            eids.append(DB.ElementId(int(s)))
        except Exception:  # noqa: BLE001
            continue
    if not eids:
        output.print_md("Nothing to clear.")
        return
    result = apply.clear_overrides(doc, doc.ActiveView, eids)
    output.print_md("Cleared **%d** highlights (%d skipped)."
                    % (result["cleared"], result["missing"]))


if __name__ == "__main__":
    main()
