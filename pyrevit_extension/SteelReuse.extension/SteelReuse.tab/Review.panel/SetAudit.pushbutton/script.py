# -*- coding: utf-8 -*-
"""Write PDA audit parameters onto the current Revit selection from a single form.

Default IronPython 3 engine, stdlib + Revit/pyRevit only, no f-strings. One WPF form replaces the old
five sequential popups (condition / verification / knockdown / recoverable length / defects). Blank
fields are left untouched on the elements. Bulk-edits every selected framing/column at once.
"""

import os

import steelreuse_apply as apply  # noqa: E402
import steelreuse_buttons as buttons  # noqa: E402
import steelreuse_pda_params as pdaparams  # noqa: E402
from pyrevit import forms, revit, script

output = script.get_output()
_XAML = os.path.join(buttons.EXT_ROOT, "lib", "steelreuse_set_audit.xaml")

_CONDITIONS = ["", "A", "B", "C", "D"]
_VERIFICATIONS = ["", "mill_cert", "coupon_tested", "documented", "visual_only", "unverified"]


class SetAuditWindow(forms.WPFWindow):
    """One form for all audit fields; OK coerces them via steelreuse_pda_params.coerce_field."""

    def __init__(self):
        forms.WPFWindow.__init__(self, _XAML)
        self.condition.ItemsSource = _CONDITIONS
        self.verification.ItemsSource = _VERIFICATIONS
        self.condition.SelectedIndex = 0
        self.verification.SelectedIndex = 0
        self.values = None
        self.okBtn.Click += self.on_ok
        self.cancelBtn.Click += self.on_cancel

    def on_ok(self, sender, args):
        raw = {"condition_grade": self.condition.SelectedItem or "",
               "verification_status": self.verification.SelectedItem or "",
               "knockdown": self.knockdown.Text,
               "recoverable_length_mm": self.recoverable.Text,
               "defects": self.defects.Text}
        self.values = dict((f, pdaparams.coerce_field(f, v)) for f, v in raw.items())
        self.Close()

    def on_cancel(self, sender, args):
        self.Close()


def main():
    doc = revit.doc
    sel = revit.get_selection()
    if not sel.element_ids:
        forms.alert("Select one or more framing/column elements first.", title="SteelReuse")
        return
    win = SetAuditWindow()
    win.ShowDialog()
    values = win.values
    if values is None:
        return  # cancelled
    if all(v is None for v in values.values()):
        forms.alert("Nothing entered.", title="SteelReuse")
        return
    result = apply.write_pda(doc, list(sel.element_ids), values)
    output.print_md("Wrote audit to **%d** elements (%d skipped). Re-extract to feed the match."
                    % (result["written"], result["missing"]))


if __name__ == "__main__":
    main()
