# -*- coding: utf-8 -*-
"""Open the donor-Review window on the Problems tab: members needing attention, with zoom-to-element.

Default IronPython 3 engine, stdlib + Revit only, no f-strings. Shells the CPython engine
(steelreuse.validate_extraction) via steelreuse_buttons.review_or_reuse to produce/reuse a fresh
review.json (the heavy work never runs in Revit -- DESIGN_PRINCIPLES hard rule 2), then hands it to
the native window. The window (lib/steelreuse_review_window.py) shows Problems + PDA QA as two tabs,
each a grid whose rows select+zoom the element in the donor model -- the in-Revit replacement for the
old browser report (which could not trace back into the model). The printable HTML is one click away.
"""

import steelreuse_buttons as buttons  # noqa: E402 -- shared engine-on-donor preamble
import steelreuse_review_window as review_window  # noqa: E402 -- the native two-tab window
import steelreuse_runner as runner  # noqa: E402
from pyrevit import forms

_EXT_ROOT = buttons.EXT_ROOT
_TAB = "problems"


def main():
    interp, donor = buttons.interpreter_and_donor(_EXT_ROOT, "Select the extracted donor.json to review")
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
