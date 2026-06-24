# -*- coding: utf-8 -*-
"""Write a blank reusable-steel inventory template (.xlsx / .csv) for a no-Revit donor inventory.

Default IronPython 3 engine, stdlib + Revit/pyRevit only, no f-strings. The actual file is written by
the CPython engine (steelreuse.cli --inventory-template) via a subprocess, so the openpyxl .xlsx path
and the column layout live in one place; this button just locates the interpreter, picks a save path,
and offers to open the result.
"""

import os

import steelreuse_buttons as buttons
import steelreuse_runner as runner
from pyrevit import forms, script

output = script.get_output()


def main():
    ext_root = buttons.EXT_ROOT
    interp = buttons.resolve_interpreter(ext_root)
    if not interp:
        return  # resolve_interpreter already alerted

    target = forms.save_file(file_ext="xlsx", default_name="donor_inventory_template")
    if not target:
        return

    res = runner.run_inventory_template(interp, target)
    if not res["ok"]:
        detail = (res.get("stdout") or res.get("stderr") or "").strip()
        hint = runner.describe_returncode(res["returncode"])
        forms.alert((hint + "\n\n" if hint else "") + (detail[-1500:] or "Template write failed."),
                    title="SteelReuse")
        return

    output.print_md("Blank inventory template written to **%s**.\n\nFill one row per reclaimed "
                    "member (headers + one worked example are included), then use it as the **Donor** "
                    "(or Demand) model in Run Match / Value Case / Review." % target)
    if forms.alert("Inventory template written to:\n%s\n\nOpen it now?" % target,
                   title="SteelReuse", ok=False, yes=True, no=True):
        try:
            os.startfile(target)
        except Exception:  # noqa: BLE001 -- non-Windows dev box / no association; the file still exists
            pass


if __name__ == "__main__":
    main()
