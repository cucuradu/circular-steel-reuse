# -*- coding: utf-8 -*-
"""IronPython-safe renderers for the review dict (problem report + PDA report), for the pyRevit
output window. Stdlib only, no f-strings. Mirrors src/steelreuse/review_view.py; reuses _esc from
steelreuse_results_view so escaping/styling stay identical. Tested in CPython via importlib path.
"""

import steelreuse_results_view as rv  # noqa: E402 -- reuse _esc + style discipline

_LEVER = {
    "UNKNOWN_SECTION": "section not recognised; rename the type or add a mapping override",
    "FUZZY_MATCH": "near-miss name; confirm via override CSV or fix the type name",
    "MISSING_GRADE": "material grade missing; a shape-family default is assumed (flagged)",
    "NO_COORDS": "no coordinates; this member cannot enter the frame analysis",
    "NOT_AUDITED": "no pre-demolition audit data; admitted at the default knockdown",
    "QUARANTINED_UNVERIFIED": "grade unverified; excluded unless --include-unverified",
    "QUARANTINED_CONDITION_D": "condition D (unsuitable); excluded from supply",
    "LOW_KNOCKDOWN": "derived/explicit knockdown below the floor; excluded from supply",
}


def render_problem_report(review):
    cov = review["coverage"]
    members = [m for m in review["members"] if m["issues"]]
    parts = ["<h2>SteelReuse extraction problems</h2>",
             "<p>%d / %d members need attention; %d unknown, %d fuzzy.</p>"
             % (len(members), cov["total"], cov["unknown"], cov["fuzzy"]),
             "<table><thead><tr><th>Element id</th><th>Role</th><th>Raw</th>"
             "<th>Mapped</th><th>Method</th><th>Issues</th></tr></thead><tbody>"]
    for m in members:
        badges = ", ".join(rv._esc(c) for c, _ in m["issues"])
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (rv._esc(m["id"]), rv._esc(m["role"]), rv._esc(m["raw_section"]),
                        rv._esc(m["section"] or "-"), rv._esc(m["mapping_method"]), badges))
    parts.append("</tbody></table>")
    return "".join(parts)


def render_pda_report(review):
    cov = review["coverage"]
    parts = ["<h2>SteelReuse pre-demolition audit (QA)</h2>",
             "<p>%d / %d audited; %d admitted, %d quarantined; avg knockdown %.3f.</p>"
             % (cov["audited"], cov["total"], cov["admitted"], cov["quarantined"],
                cov["avg_knockdown"]),
             "<table><thead><tr><th>Element id</th><th>Role</th><th>Condition</th>"
             "<th>Verification</th><th>Knockdown</th><th>Admitted</th><th>Defects</th>"
             "</tr></thead><tbody>"]
    for m in review["members"]:
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%.3f</td>"
                     "<td>%s</td><td>%s</td></tr>"
                     % (rv._esc(m["id"]), rv._esc(m["role"]), rv._esc(m["condition"] or "-"),
                        rv._esc(m["verification"] or "-"), m["knockdown"],
                        "yes" if m["admitted"] else "no", rv._esc(m["defects"] or "")))
    parts.append("</tbody></table>")
    return "".join(parts)
