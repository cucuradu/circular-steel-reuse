# -*- coding: utf-8 -*-
"""Run a headless extraction review and show the problem report in pyRevit's output window.

Default IronPython 3 engine, stdlib only, no f-strings. Shells out to the CPython engine
(steelreuse.validate_extraction) via steelreuse_runner -- the heavy work never runs in Revit
(DESIGN_PRINCIPLES hard rule 2). Element ids are linkified so a click selects/zooms the element.
"""

import json
import os

import steelreuse_review_view as reviewview  # noqa: E402 -- thin IronPython view, see below
import steelreuse_runner as runner  # noqa: E402 -- extension lib/ is on the path
from pyrevit import DB, forms, script

output = script.get_output()
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _interp():
    saved = runner.load_settings(_EXT_ROOT).get("interpreter")
    interp = runner.discover_interpreter(saved, _EXT_ROOT)
    if not interp:
        forms.alert("No working Python interpreter found. Set one in Run Match first.",
                    title="SteelReuse")
    return interp


def _donor_json():
    last = runner.load_settings(_EXT_ROOT).get("last_donor")
    if last and os.path.isfile(last):
        return last
    return forms.pick_file(file_ext="json", title="Select the extracted donor.json to review")


def main():
    interp = _interp()
    donor = _donor_json()
    if not interp or not donor:
        return
    out_dir = os.path.join(_EXT_ROOT, "steelreuse_reports")
    res = runner.run_review(interp, {"donor": donor}, out_dir)
    if not res["ok"]:
        detail = (res["stdout"] or res["stderr"] or "").strip()
        hint = runner.describe_returncode(res["returncode"])
        if hint:
            log = res["paths"].get("log")
            detail = hint + (("\n\nLog: " + log) if log else "") + (("\n\n" + detail) if detail else "")
        forms.alert("Review failed (exit %s):\n\n%s" % (res["returncode"], detail[-1500:] or "(no output)"),
                    title="SteelReuse")
        return
    with open(res["paths"]["review_json"], encoding="utf-8") as handle:
        review = json.load(handle)
    _print_summary_md(review)   # plain-text first: visible even if the HTML report doesn't render
    output.print_html(reviewview.render_problem_report(review))
    _print_linkified(review)


def _print_summary_md(review):
    """A plain-markdown coverage line, so the output window is never blank if the HTML report fails
    to render in this pyRevit/Revit build."""
    cov = review.get("coverage", {})
    members = review.get("members", [])
    n_problem = sum(1 for m in members if m.get("worst_severity"))
    output.print_md("## Extraction review")
    output.print_md("**%s** members  |  **%s** mapped  |  **%s** with coordinates  |  **%d** with issues"
                    % (cov.get("total", len(members)), cov.get("mapped", "?"),
                       cov.get("with_coords", "?"), n_problem))


def _print_linkified(review):
    """A clickable element list grouped by severity (same idea as Apply Matches' attention list)."""
    buckets = {"error": [], "warn": [], "info": []}
    for m in review["members"]:
        if m["worst_severity"]:
            buckets[m["worst_severity"]].append(m["id"])
    for sev in ("error", "warn", "info"):
        ids = buckets[sev]
        if not ids:
            continue
        output.print_md("**%s (%d)** — click to select:" % (sev, len(ids)))
        links = []
        for eid in ids:
            try:
                links.append(output.linkify(DB.ElementId(int(eid))))
            except Exception:  # non-numeric id (e.g. IFC) -- just show the text
                links.append(eid)
        output.print_html(" ".join(links))


if __name__ == "__main__":
    main()
