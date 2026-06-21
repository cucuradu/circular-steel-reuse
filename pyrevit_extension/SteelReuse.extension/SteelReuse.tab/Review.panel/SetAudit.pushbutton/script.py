# -*- coding: utf-8 -*-
"""Write PDA shared parameters onto the current Revit selection from a small form.

Default IronPython 3 engine, stdlib + Revit/pyRevit only, no f-strings. Bulk-edits every selected
framing/column at once; blank fields are left untouched on the elements.
"""

import steelreuse_apply as apply  # noqa: E402
import steelreuse_pda_params as pdaparams  # noqa: E402
from pyrevit import forms, revit, script

output = script.get_output()

_CONDITIONS = ["", "A", "B", "C", "D"]
_VERIFICATIONS = ["", "mill_cert", "coupon_tested", "documented", "visual_only", "unverified"]


def _ask():
    """Collect field -> raw string from the user (a simple sequence of prompts)."""
    cond = forms.ask_for_one_item(_CONDITIONS, default="", prompt="Condition grade (A-D)",
                                  title="Set Audit 1/5")
    if cond is None:
        return None
    ver = forms.ask_for_one_item(_VERIFICATIONS, default="", prompt="Verification basis",
                                 title="Set Audit 2/5")
    if ver is None:
        return None
    kd = forms.ask_for_string(default="", prompt="Explicit knockdown (blank = derive)",
                              title="Set Audit 3/5")
    rl = forms.ask_for_string(default="", prompt="Recoverable length mm (blank = full length)",
                              title="Set Audit 4/5")
    defects = forms.ask_for_string(default="", prompt="Defects (free text)", title="Set Audit 5/5")
    raw = {"condition_grade": cond, "verification_status": ver, "knockdown": kd,
           "recoverable_length_mm": rl, "defects": defects}
    return dict((f, pdaparams.coerce_field(f, v)) for f, v in raw.items())


def main():
    doc = revit.doc
    sel = revit.get_selection()
    if not sel.element_ids:
        forms.alert("Select one or more framing/column elements first.", title="SteelReuse")
        return
    values = _ask()
    if values is None:
        return
    if all(v is None for v in values.values()):
        forms.alert("Nothing entered.", title="SteelReuse")
        return
    result = apply.write_pda(doc, list(sel.element_ids), values)
    output.print_md("Wrote audit to **%d** elements (%d skipped). Re-extract to feed the match."
                    % (result["written"], result["missing"]))


if __name__ == "__main__":
    main()
