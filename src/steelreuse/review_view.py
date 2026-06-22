# src/steelreuse/review_view.py
"""Pure renderers for the extraction review: problem report + PDA QA report, HTML and CSV.

Dict-in / string-out (the dict is ReviewModel.to_dict()), so the CLI renders static files and the
tests render in CPython. Navigation in Revit is done by the pushbutton via output.linkify on the
problem element-ids (same mechanism Apply Matches uses), so these stay pure static generators with
no Revit dependency. HTML styling/filter JS mirrors lib/steelreuse_results_view.py.
"""

from __future__ import annotations

import csv
import io

from .extraction_review import ISSUE_LEVER

_STYLE = """<style>
.srx h2 { margin: 0.2em 0; }
.srx .kpi { display:inline-block; margin-right:1.4em; font-size:1.05em; }
.srx .kpi b { font-size:1.25em; }
.srx table { border-collapse:collapse; width:100%; font-size:0.93em; }
.srx th, .srx td { border:1px solid #ccc; padding:3px 6px; text-align:left; }
.srx th { background:#eee; }
.srx .filters { margin:0.7em 0; padding:0.5em; background:#f3f3f3; border-radius:6px; }
.srx .filters label { margin-right:1em; }
.srx .sev-error { color:#c33; font-weight:bold; }
.srx .sev-warn { color:#b80; font-weight:bold; }
.srx .sev-info { color:#555; }
</style>"""

_FILTER_JS = """<script>
function srxFilter(){
  var role=document.getElementById('srx-filter-role').value;
  var sev=document.getElementById('srx-filter-severity').value;
  var rows=document.getElementsByClassName('srx-row');
  for(var i=0;i<rows.length;i++){
    var r=rows[i], show=true;
    if(role && r.getAttribute('data-role')!==role) show=false;
    if(sev && r.getAttribute('data-severity')!==sev) show=false;
    r.style.display=show?'':'none';
  }
}
</script>"""

_AUDIT_COLUMNS = ["id", "condition_grade", "verification_status",
                  "knockdown", "recoverable_length_mm", "defects"]


def _esc(value):
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _issue_badges(issues):
    out = []
    for code, sev in issues:
        title = ISSUE_LEVER.get(code, "")
        out.append(f'<span class="sev-{sev}" title="{_esc(title)}">{_esc(code)}</span>')
    return ", ".join(out)


def render_problem_report(review):
    """ReviewModel dict -> a self-contained HTML problem report (members with issues only)."""
    cov = review["coverage"]
    members = [m for m in review["members"] if m["issues"]]
    header = (
        '<h2>SteelReuse extraction problems</h2>'
        f'<div><span class="kpi"><b>{len(members)}</b> / {cov["total"]} members need attention</span>'
        f'<span class="kpi"><b>{cov["unknown"]}</b> unknown</span>'
        f'<span class="kpi"><b>{cov["fuzzy"]}</b> fuzzy</span></div>'
    )

    roles = sorted({m["role"] for m in members})
    role_opts = "".join(f'<option value="{_esc(r)}">{_esc(r)}</option>' for r in roles)
    filters = (
        '<div class="filters">'
        '<label>Role <select id="srx-filter-role" onchange="srxFilter()">'
        f'<option value="">all</option>{role_opts}</select></label>'
        '<label>Severity <select id="srx-filter-severity" onchange="srxFilter()">'
        '<option value="">all</option><option value="error">error</option>'
        '<option value="warn">warn</option><option value="info">info</option>'
        '</select></label></div>'
    )

    head = ('<table><thead><tr><th>Element id</th><th>Role</th><th>Raw section</th>'
            '<th>Mapped</th><th>Method</th><th>Issues</th></tr></thead><tbody id="srx-rows">')
    body = []
    for m in members:
        body.append(
            f'<tr class="srx-row" data-role="{_esc(m["role"])}" '
            f'data-severity="{_esc(m["worst_severity"])}">'
            f'<td>{_esc(m["id"])}</td><td>{_esc(m["role"])}</td>'
            f'<td>{_esc(m["raw_section"])}</td><td>{_esc(m["section"] or "—")}</td>'
            f'<td>{_esc(m["mapping_method"])}</td><td>{_issue_badges(m["issues"])}</td></tr>')
    table = head + "".join(body) + "</tbody></table>"
    return "\n".join([_STYLE, '<div class="srx">', header, filters, table, "</div>", _FILTER_JS])


def render_pda_report(review):
    """ReviewModel dict -> a self-contained HTML PDA QA report (audit coverage + per-member)."""
    cov = review["coverage"]
    header = (
        '<h2>SteelReuse pre-demolition audit (QA)</h2>'
        f'<div><span class="kpi"><b>{cov["audited"]}</b> / {cov["total"]} audited</span>'
        f'<span class="kpi"><b>{cov["admitted"]}</b> admitted</span>'
        f'<span class="kpi"><b>{cov["quarantined"]}</b> quarantined</span>'
        f'<span class="kpi">avg knockdown <b>{cov["avg_knockdown"]:.3f}</b></span></div>'
    )

    head = ('<table><thead><tr><th>Element id</th><th>Role</th><th>Condition</th>'
            '<th>Verification</th><th>Knockdown</th><th>Admitted</th>'
            '<th>Recoverable (mm)</th><th>Defects</th><th>Connection</th><th>Degree</th>'
            '</tr></thead><tbody>')
    body = []
    for m in review["members"]:
        rl = m["recoverable_length_mm"]
        recoverable = _esc(f"{rl:.0f}" if rl is not None else "—")
        conn = m.get("connection_type") or "—"
        deg = m.get("degree")
        body.append(
            f'<tr><td>{_esc(m["id"])}</td><td>{_esc(m["role"])}</td>'
            f'<td>{_esc(m["condition"] or "—")}</td><td>{_esc(m["verification"] or "—")}</td>'
            f'<td>{m["knockdown"]:.3f}</td><td>{"yes" if m["admitted"] else "no"}</td>'
            f'<td>{recoverable}</td><td>{_esc(m["defects"] or "")}</td>'
            f'<td>{_esc(conn)}</td><td>{_esc("—" if deg is None else deg)}</td></tr>')
    table = head + "".join(body) + "</tbody></table>"

    needs = [m for m in review["members"]
             if not m["audited"] or m["condition"].upper() == "D"
             or (m["audited"] and not m["admitted"])]
    note = ""
    if needs:
        items = "".join(f"<li>{_esc(m['id'])} — {_esc(_needs_reason(m))}</li>" for m in needs)
        note = f"<h3>Needs attention</h3><ul>{items}</ul>"
    return "\n".join([_STYLE, '<div class="srx">', header, table, note, "</div>"])


def _needs_reason(member):
    if not member["audited"]:
        return "not audited"
    if member["condition"].upper() == "D":
        return "condition D"
    return "quarantined"


def problem_report_csv(review):
    """CSV of members with issues: id, role, mapping_method, section, issues (codes joined)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "role", "mapping_method", "section", "issues"])
    for m in review["members"]:
        if not m["issues"]:
            continue
        w.writerow([m["id"], m["role"], m["mapping_method"], m["section"] or "",
                    ";".join(code for code, _ in m["issues"])])
    return buf.getvalue()


def pda_report_csv(review):
    """CSV in the exact --pda column order, so it round-trips into core.audit.load_audit_csv."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_AUDIT_COLUMNS)
    w.writeheader()
    for m in review["members"]:
        rl = m["recoverable_length_mm"]
        w.writerow({
            "id": m["id"],
            "condition_grade": m["condition"] or "",
            "verification_status": m["verification"] or "",
            "knockdown": "" if m["knockdown"] == 1.0 and not m["audited"] else m["knockdown"],
            "recoverable_length_mm": "" if rl is None else f"{rl:.0f}",
            "defects": m["defects"] or "",
        })
    return buf.getvalue()
