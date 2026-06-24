# -*- coding: utf-8 -*-
"""Colour the active view by extraction-review severity (reuses steelreuse_apply overrides).

Shares a fresh review.json with Problems / PDA Report instead of re-running the engine.
"""

import steelreuse_apply as apply  # noqa: E402
import steelreuse_buttons as buttons  # noqa: E402
import steelreuse_runner as runner  # noqa: E402
from pyrevit import revit, script

output = script.get_output()
_EXT_ROOT = buttons.EXT_ROOT


def main():
    doc = revit.doc
    interp, donor = buttons.interpreter_and_donor(_EXT_ROOT)
    if not interp:
        return  # interpreter_and_donor already alerted
    if not donor:
        output.print_md("No donor model selected. Run **Extract** first, then try again.")
        return
    review, err = buttons.review_or_reuse(_EXT_ROOT, interp, donor)
    if err is not None:
        output.print_md("**Review failed** (exit %s):\n\n```\n%s\n```"
                        % (err["returncode"], (err["stdout"] or err["stderr"] or "").strip()[-1500:]))
        return
    result = apply.apply_review_overrides(doc, doc.ActiveView, review)
    # Highlight Problems writes no parameter, so the saved id list is Clear's ONLY record of which
    # elements it coloured. ACCUMULATE (union) across runs -- overwriting would orphan an earlier
    # highlight so Clear could only undo the most recent one. Clear resets the list to [] when it runs.
    previous = runner.load_highlight(_EXT_ROOT)
    new_ids = [str(m["id"]) for m in review["members"] if m.get("color")]
    merged = list(dict.fromkeys(list(previous) + new_ids))  # de-duped, order preserved
    runner.save_highlight(_EXT_ROOT, merged)
    # Migrate the legacy id list out of the settings config so every other button stops parsing it.
    settings = runner.load_settings(_EXT_ROOT)
    if "highlighted_ids" in settings:
        del settings["highlighted_ids"]
        runner.save_settings(_EXT_ROOT, settings)
    output.print_md("Highlighted **%d** elements (%d not in this model). Use the **Clear** button "
                    "(Match panel) to undo." % (result["applied"], result["missing"]))


if __name__ == "__main__":
    main()
