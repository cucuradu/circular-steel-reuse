# -*- coding: utf-8 -*-
"""Render a match's results.json into an HTML view for pyRevit's output window.

Pure + stdlib only (IronPython-safe), so it is fully unit-tested and the Run Match / Results buttons
just hand it the parsed results.json and call ``output.print_html(...)``.

The view = a KPI header, three display filters (section text, status, minimum utilisation) wired to a
small vanilla-JS row toggler, the assignments table, then the unfilled-slots and quarantined-donor
lists. No element selection/zoom here -- that is the native dockable panel's job; this window is for
reviewing and filtering the match.
"""


def _esc(value):
    """Minimal HTML escape for free text (reasons, section names)."""
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(value, fmt):
    """Format a number, or an em dash when it is None."""
    if value is None:
        return "—"
    return fmt % value


_STYLE = """<style>
.srx h2 { margin: 0.2em 0; }
.srx .kpi { display:inline-block; margin-right:1.4em; font-size:1.05em; }
.srx .kpi b { font-size:1.25em; }
.srx .badge { padding:1px 7px; border-radius:9px; font-size:0.85em; }
.srx .ok { background:#0a6; color:#fff; }
.srx .warn { background:#c33; color:#fff; }
.srx .filters { margin:0.7em 0; padding:0.5em; background:#f3f3f3; border-radius:6px; }
.srx .filters label { margin-right:1em; }
.srx table { border-collapse:collapse; width:100%; font-size:0.93em; }
.srx th, .srx td { border:1px solid #ccc; padding:3px 6px; text-align:left; }
.srx th { background:#eee; }
.srx .review { color:#c33; font-weight:bold; }
.srx .rules { font-size:0.88em; color:#444; background:#f7f7f7; padding:4px 8px; border-radius:5px; }
.srx .note { font-size:0.9em; color:#333; }
</style>"""

_FILTER_JS = """<script>
function srxFilter(){
  var sec=(document.getElementById('srx-filter-section').value||'').toUpperCase();
  var st=document.getElementById('srx-filter-status').value;
  var mu=parseFloat(document.getElementById('srx-filter-util').value)||0;
  var rows=document.getElementsByClassName('srx-row');
  for(var i=0;i<rows.length;i++){
    var r=rows[i], show=true;
    if(sec && r.getAttribute('data-section').indexOf(sec)<0) show=false;
    if(st==='CONN'){ if(r.getAttribute('data-conn')!=='1') show=false; }
    else if(st && r.getAttribute('data-status')!==st) show=false;
    if(parseFloat(r.getAttribute('data-util'))<mu) show=false;
    r.style.display=show?'':'none';
  }
}
</script>"""


def _kpi_header(kpis):
    proven = kpis.get("proven_optimal")
    badge = '<span class="badge ok">proven optimal</span>' if proven \
        else '<span class="badge warn">heuristic (not proven optimal)</span>'
    return (
        '<h2>SteelReuse match results</h2>'
        '<div>'
        '<span class="kpi"><b>%s</b> / %s slots reused</span>'
        '<span class="kpi"><b>%s</b> kg CO2e saved</span>'
        '<span class="kpi">objective: <b>%s</b></span>'
        '<span class="kpi">%s</span>'
        '</div>'
    ) % (kpis.get("reused", "?"), kpis.get("slots", "?"),
         _num(kpis.get("co2_saved_kg"), "%.0f"), _esc(kpis.get("objective", "?")), badge)


def _filters():
    return (
        '<div class="filters">'
        '<label>Section <input id="srx-filter-section" oninput="srxFilter()" '
        'placeholder="e.g. W18"></label>'
        '<label>Status <select id="srx-filter-status" onchange="srxFilter()">'
        '<option value="">all</option><option value="OK">OK</option>'
        '<option value="REVIEW">review</option>'
        '<option value="CONN">connection review</option></select></label>'
        '<label>Min utilisation <input id="srx-filter-util" type="number" step="0.05" '
        'min="0" max="1" value="0" oninput="srxFilter()" style="width:5em"></label>'
        '</div>'
    )


def _chi_cell(row):
    chi = row.get("chi_lt")
    free = row.get("chi_lt_if_free")
    cell = _num(chi, "%.2f")
    # Mirror the report: a restrained beam that would fail unrestrained relies on the slab -- flag it.
    if chi == 1.0 and free is not None and free < 0.85:
        cell += ' <span class="review" title="would be %.2f if the flange were unrestrained">&#9888;</span>' % free
    return cell


def _alt_cell(r):
    """Tier 3 'next best' cell: the runner-up donor for this slot + the net-CO2 margin."""
    alt = r.get("alt_donor_id")
    if not alt:
        return "&mdash;"
    margin = r.get("alt_margin_kg")
    txt = "%s (&Delta;%s kg)" % (_esc(alt), _num(margin, "%.0f"))
    if r.get("alt_used_elsewhere"):
        txt += ' <span class="review" title="this runner-up was reused on another slot">used elsewhere</span>'
    return txt


def _assignments_table(rows):
    head = ("<table><thead><tr>"
            "<th>Demand id</th><th>Demand</th><th>Donor id</th><th>Donor</th>"
            "<th>Util</th><th>Status</th><th>&chi;LT</th><th>Conn</th><th>CO2e kg</th>"
            "<th>Next best</th>"
            "</tr></thead><tbody id=\"srx-rows\">")
    body = []
    for r in rows:
        data_section = (_esc(r.get("demand_section", "")) + " " + _esc(r.get("donor_section", ""))).upper()
        conn = "1" if r.get("connection_review") else "0"
        conn_cell = '<span class="review">review</span>' if r.get("connection_review") else ""
        body.append(
            ('<tr class="srx-row" data-section="%s" data-status="%s" data-conn="%s" data-util="%s">'
             '<td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
             '<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>')
            % (data_section, _esc(r.get("check_status", "")), conn, r.get("utilization", 0),
               _esc(r.get("demand_id", "")), _esc(r.get("demand_section", "")),
               _esc(r.get("donor_id", "")), _esc(r.get("donor_section", "")),
               _num(r.get("utilization"), "%.2f"), _esc(r.get("check_status", "")),
               _chi_cell(r), conn_cell, _num(r.get("co2_saved_kg"), "%.0f"), _alt_cell(r)))
    return head + "".join(body) + "</tbody></table>"


def _simple_table(title, headers, rows_html):
    if not rows_html:
        return ""
    heading = ("<h3>%s</h3>" % _esc(title)) if title else ""
    head = "%s<table><thead><tr>%s</tr></thead><tbody>" % (
        heading, "".join("<th>%s</th>" % _esc(h) for h in headers))
    return head + "".join(rows_html) + "</tbody></table>"


def _unfilled_section(unfilled):
    rows = ["<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_esc(u.get("demand_id", "")), _esc(u.get("demand_section", "")),
               _esc(u.get("reason_detail", "")))
            for u in unfilled]
    return _simple_table("Unfilled demand slots (need new steel)",
                         ["Demand id", "Section", "Why unfilled"], rows)


def _quarantine_section(quarantined):
    rows = ["<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_esc(q.get("donor_id", "")), _esc(q.get("donor_section", "")), _esc(q.get("reason", "")))
            for q in quarantined]
    return _simple_table("Quarantined donors (excluded from matching)",
                         ["Donor id", "Section", "Reason"], rows)


def _rules_line(rules):
    """One-line stamp of the rule-data versions the run used (Roadmap §1.2), or '' if absent."""
    if not rules:
        return ""
    tables = ", ".join("%s v%s" % (_esc(t.get("name", "")), _esc(t.get("version", "")))
                       for t in rules.get("tables", []))
    return ('<p class="rules"><b>Rule data:</b> ruleset v%s &nbsp;|&nbsp; %s &nbsp;|&nbsp; '
            'carbon factors v%s</p>') % (
        _esc(rules.get("ruleset_version", "?")), tables,
        _esc(rules.get("carbon_factors_version", "?")))


def _mismatch_section(mismatch):
    """Donor-row provenance: a 100%-coverage summary + the per-donor classified-with-a-reason table."""
    if not mismatch:
        return ""
    summary = mismatch.get("summary", {})
    rows = mismatch.get("rows", [])
    cover = "100%" if summary.get("accounts_for_all") else "INCOMPLETE"
    head = (
        '<h3>Donor provenance (mismatch log)</h3>'
        '<p class="note"><b>%s</b> donor row(s) accounted for (%s): '
        '<b>%s</b> mapped / <b>%s</b> fuzzy / <b>%s</b> unknown / <b>%s</b> quarantined. '
        'Every donor is classified with a reason, so nothing is silently dropped.</p>'
    ) % (summary.get("n_donor_rows", "?"), cover, summary.get("mapped", 0),
         summary.get("fuzzy", 0), summary.get("unknown", 0), summary.get("quarantined", 0))
    body = []
    for r in rows:
        cls = _esc(r.get("classification", ""))
        css = ' class="review"' if cls in ("fuzzy", "unknown", "quarantined") else ""
        body.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td%s>%s</td><td>%s</td><td>%s</td></tr>"
            % (_esc(r.get("id", "")), _esc(r.get("raw_section", "")), _esc(r.get("canonical", "") or ""),
               css, cls, _esc(r.get("outcome", "")), _esc(r.get("reason", ""))))
    table = _simple_table("", ["Donor id", "Raw name", "Section", "Class", "Outcome", "Reason"], body)
    return head + table


def render_results_html(data):
    """results.json dict -> a self-contained HTML string for ``output.print_html``."""
    kpis = data.get("kpis", {})
    parts = [_STYLE, '<div class="srx">', _kpi_header(kpis),
             _rules_line(data.get("rules")), _filters(),
             _assignments_table(data.get("assignments", [])),
             _unfilled_section(data.get("unfilled", [])),
             _quarantine_section(data.get("quarantined_donors", [])),
             _mismatch_section(data.get("mismatch")),
             '</div>', _FILTER_JS]
    return "\n".join(parts)
