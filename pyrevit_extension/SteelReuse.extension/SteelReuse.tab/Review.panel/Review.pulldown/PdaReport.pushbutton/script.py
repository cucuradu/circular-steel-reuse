# -*- coding: utf-8 -*-
"""Open the donor-Review window on the PDA QA tab: the pre-demolition audit, with zoom-to-element.

Default IronPython 3 engine, stdlib + Revit only, no f-strings. Reuses the SAME review.json as the
Problems tab (steelreuse_buttons.review_or_reuse shares one engine run), then opens the native
two-tab window (lib/steelreuse_review_window.py) on the PDA QA tab. The printable HTML report is one
click away (Open report).
"""

import steelreuse_buttons as buttons  # noqa: E402
import steelreuse_review_window as review_window  # noqa: E402
import steelreuse_runner as runner  # noqa: E402
from pyrevit import forms

_EXT_ROOT = buttons.EXT_ROOT
_TAB = "pda"


def main():
    interp, donor = buttons.interpreter_and_donor(_EXT_ROOT)
    if not interp:
        return  # interpreter_and_donor already alerted
    if not donor:
        forms.alert("No donor model selected. Run Extract first, then try again.", title="SteelReuse")
        return
    review, err = buttons.review_or_reuse(_EXT_ROOT, interp, donor)
    if err is not None:
        detail = (err["stdout"] or err["stderr"] or "").strip()
        hint = runner.describe_returncode(err["returncode"])
        msg = ["Review failed (exit %s)." % err["returncode"]]
        if hint:
            msg.append(hint)
        log = err["paths"].get("log")
        if log:
            msg.append("Log: " + log)
        if detail:
            msg.append(detail[-1200:])
        forms.alert("\n\n".join(msg), title="SteelReuse")
        return
    review_window.ReviewWindow(_EXT_ROOT, review, tab=_TAB).show()  # modeless


if __name__ == "__main__":
    main()
