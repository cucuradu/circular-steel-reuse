# -*- coding: utf-8 -*-
"""Colour the active model's donor elements by reuse / review / scrap suitability.

Default IronPython 3 engine, stdlib only, no f-strings, %-formatting. Shells the heavy work to the
signed CPython venv via steelreuse.value_case_cli (DESIGN_PRINCIPLES hard rule 2). No demand model
needed -- the whole point is a standalone pre-decision tool.

Green  = REUSE   (reuse-ready: mapped, grade verified, sound condition)
Amber  = REVIEW  (reusable but needs a coupon test / inspection before structural reliance)
Grey   = SCRAP   (not reusable: quarantined by the audit, or unmapped -> recycle)

Reports the reuse PRIZE (reclaimed value + premium over scrap + CO2 saved). Deconstruction cost
(soft-strip, scaffolding, asbestos) is out of scope -- the contractor weighs that themselves.
Market-price defaults (BCSA/MEPS 2024): scrap 240/t, reclaimed 950/t. Customise via the CLI.
"""

import json
import os

import steelreuse_apply as apply_mod  # noqa: E402 -- extension lib/ is on the path
import steelreuse_buttons as buttons  # noqa: E402 -- shared engine-on-donor preamble
import steelreuse_runner as runner  # noqa: E402
from pyrevit import forms, revit

doc = revit.doc

# .../ValueCase.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_OUT_DIR = runner.reports_dir(_EXT_ROOT)


def _skipped_line(kpis):
    """One-line note on members that were not assessed (foundations / unrecognised sections)."""
    total = kpis.get("skipped_total", 0)
    if not total:
        return ""
    breakdown = kpis.get("skipped_breakdown") or {}
    parts = []
    for key in sorted(breakdown):
        parts.append("%d %s" % (breakdown[key], key))
    detail = (" (" + ", ".join(parts) + ")") if parts else ""
    return "Skipped %d not assessed%s." % (total, detail)


def main():
    interp, donor = buttons.interpreter_and_donor(_EXT_ROOT)
    if not interp or not donor:
        return

    res = runner.run_value_case(interp, {"donor": donor}, _OUT_DIR)
    if not res["ok"]:
        detail = (res["stdout"] or res["stderr"] or "").strip()
        hint = runner.describe_returncode(res["returncode"])
        if hint:
            log = res["paths"].get("log")
            detail = (hint
                      + (("\n\nLog: " + log) if log else "")
                      + (("\n\n" + detail) if detail else ""))
        forms.alert("Value case failed (exit %s):\n\n%s"
                    % (res["returncode"], detail[-1500:] or "(no output)"),
                    title="SteelReuse")
        return

    wb_path = res["paths"]["writeback"]
    if not os.path.isfile(wb_path):
        forms.alert(
            "Engine returned OK but value_case.json was not written.\nLog: %s"
            % res["paths"].get("log", ""),
            title="SteelReuse")
        return

    with open(wb_path) as handle:
        data = json.load(handle)
    members = data.get("members", {})
    kpis = data.get("kpis", {})

    # Colour the active view + write the Reuse VC parameters (so the schedule can show detail).
    view = doc.ActiveView
    apply_mod.apply_value_case(doc, view, members)

    reuse = kpis.get("reuse_count", 0)
    review = kpis.get("review_count", 0)
    scrap = kpis.get("scrap_count", 0)
    reusable_t = kpis.get("reusable_mass_kg", 0.0) / 1000.0
    reclaimed = kpis.get("total_reclaimed_value_gbp", 0.0)
    premium = kpis.get("total_reuse_premium_gbp", 0.0)
    co2 = kpis.get("total_co2_saved_kg", 0.0)
    skipped = _skipped_line(kpis)

    # Totals-only popup. Per-member detail lives in the schedule (Schedule -> Value Case).
    lines = [
        "Coloured this model by reuse suitability.",
        "",
        "REUSE    %d   green   (reuse-ready: verified, sound)" % reuse,
        "REVIEW   %d   amber   (reusable -- needs a test/inspection first)" % review,
        "SCRAP    %d   grey    (not reusable -> recycle)" % scrap,
        "",
        "Reusable steel:  %.1f t" % reusable_t,
        "Reuse prize:  %.0f GBP reclaimed value (%.0f GBP above scrap)" % (reclaimed, premium),
        "CO2 avoided vs new steel:  %.0f kg" % co2,
    ]
    if review:
        lines += ["", "%d member(s) need grade/condition verification before reuse." % review]
    if skipped:
        lines += ["", skipped]
    lines += [
        "",
        "Deconstruction cost (soft-strip, scaffolding, etc.) is out of scope -- weigh the",
        "prize above against your own deconstruction estimate.",
        "",
        "Per-member detail + the reason for each verdict:",
        "Schedule button -> Value Case.",
    ]
    forms.alert("\n".join(lines), title="SteelReuse Value Case")


if __name__ == "__main__":
    main()
