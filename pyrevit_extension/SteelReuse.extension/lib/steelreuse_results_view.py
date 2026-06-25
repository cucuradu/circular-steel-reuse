# -*- coding: utf-8 -*-
"""Render a match's results.json into an HTML view for pyRevit's output window.

Pure + stdlib only (IronPython-safe), so it is fully unit-tested and the Run Match / Results buttons
just hand it the parsed results.json and call ``output.print_html(...)``.

The view = a KPI header (with a reuse-rate bar), the "why" diagnosis box, three display filters
(section text, status, minimum utilisation) wired to a small vanilla-JS row toggler + click-to-sort
column headers, the assignments table, then the unfilled-slots, warnings, and (when present)
disposition / donor what-if value / pareto / portfolio / quarantine / provenance / audit sections.
No element selection/zoom here -- that is the native dockable panel's job; this window is for
reviewing, filtering and sorting the match.
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
.srx h3 { margin: 0.9em 0 0.2em; }
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
.srx th.srx-sort { cursor:pointer; }
.srx th.srx-sort:hover { background:#dde6ee; }
.srx .review { color:#c33; font-weight:bold; }
.srx .rules { font-size:0.88em; color:#444; background:#f7f7f7; padding:4px 8px; border-radius:5px; }
.srx .note { font-size:0.9em; color:#333; }
.srx .ok-note { color:#0a6; font-weight:bold; }
.srx .diag { margin:0.6em 0; padding:8px 12px; background:#fff7e6; border-left:4px solid #e0a020;
             border-radius:4px; font-size:0.95em; }
.srx .bar { position:relative; height:18px; width:280px; max-width:100%; background:#e6e6e6;
            border-radius:9px; margin:0.5em 0; overflow:hidden; }
.srx .bar-fill { height:100%; background:#0a6; border-radius:9px 0 0 9px; }
.srx .bar-label { position:absolute; left:8px; top:0; line-height:18px; font-size:0.8em; color:#222; }
.srx .u-low { color:#888; } .srx .u-ok { color:#0a6; font-weight:bold; }
.srx .u-high { color:#b8860b; font-weight:bold; } .srx .u-over { color:#c33; font-weight:bold; }
.srx button.srx-btn { margin-left:1em; padding:2px 9px; cursor:pointer; }
.srx .copied { color:#0a6; margin-left:0.6em; font-size:0.85em; }
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
    else if(st==='ALT'){ if(r.getAttribute('data-alt')!=='1') show=false; }
    else if(st && r.getAttribute('data-status')!==st) show=false;
    if(parseFloat(r.getAttribute('data-util'))<mu) show=false;
    r.style.display=show?'':'none';
  }
}
function srxCsv(){
  var rows=document.getElementsByClassName('srx-row');
  var head=['Demand id','Demand','Donor id','Donor','Util','Status','chiLT','Conn','CO2e kg','Next best'];
  var nl=String.fromCharCode(10), q=String.fromCharCode(34);
  var lines=[head.join(',')], n=0;
  for(var i=0;i<rows.length;i++){
    if(rows[i].style.display==='none') continue;
    var cells=rows[i].children, vals=[];
    for(var c=0;c<cells.length;c++){
      var t=(cells[c].textContent||'').trim().split(q).join(q+q);
      if(t.indexOf(',')>=0||t.indexOf(q)>=0||t.indexOf(nl)>=0){ t=q+t+q; }
      vals.push(t);
    }
    lines.push(vals.join(',')); n++;
  }
  var csv=lines.join(nl), note=document.getElementById('srx-csv-note');
  function done(){ if(note){ note.textContent='copied '+n+' row(s)'; } }
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(csv).then(done, function(){ srxCsvFallback(csv,done); });
  } else { srxCsvFallback(csv,done); }
}
function srxCsvFallback(csv,done){
  var ta=document.createElement('textarea'); ta.value=csv;
  document.body.appendChild(ta); ta.select();
  try{ document.execCommand('copy'); done(); }catch(e){}
  document.body.removeChild(ta);
}
function srxSort(th){
  var table=th.parentNode.parentNode.parentNode;        // th -> tr -> thead -> table
  var tbody=table.getElementsByTagName('tbody')[0];
  var cells=th.parentNode.children, idx=0;
  for(var c=0;c<cells.length;c++){ if(cells[c]===th){ idx=c; break; } }
  var asc=th.getAttribute('data-asc')!=='1'; th.setAttribute('data-asc',asc?'1':'0');
  var rows=[], trs=tbody.getElementsByTagName('tr');
  for(var i=0;i<trs.length;i++){ rows.push(trs[i]); }
  rows.sort(function(a,b){
    var x=(a.children[idx].textContent||'').trim(), y=(b.children[idx].textContent||'').trim();
    var nx=parseFloat(x), ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny)){ return asc?nx-ny:ny-nx; }
    return asc?(x<y?-1:x>y?1:0):(x>y?-1:x<y?1:0);
  });
  for(var j=0;j<rows.length;j++){ tbody.appendChild(rows[j]); }
}
</script>"""


def _kpi_header(kpis):
    proven = kpis.get("proven_optimal")
    badge = '<span class="badge ok">proven optimal</span>' if proven \
        else '<span class="badge warn">heuristic (not proven optimal)</span>'
    reused = kpis.get("reused", 0) or 0
    slots = kpis.get("slots", 0) or 0
    pct = kpis.get("reuse_rate_pct")
    if pct is None:
        pct = int(round(100.0 * reused / slots)) if slots else 0
    # Optional KPIs only present in richer runs -- shown when available, skipped otherwise.
    extra = ""
    if kpis.get("mass_reused_kg") is not None:
        extra += '<span class="kpi"><b>%s</b> kg reused</span>' % _num(kpis.get("mass_reused_kg"), "%.0f")
    if kpis.get("distinct_sections") is not None:
        extra += '<span class="kpi"><b>%s</b> distinct section(s)</span>' % kpis.get("distinct_sections")
    if kpis.get("donor_saved_co2_kg") is not None:
        extra += ('<span class="kpi"><b>%s</b> kg CO2e in full stock</span>'
                  % _num(kpis.get("donor_saved_co2_kg"), "%.0f"))
    bar = ('<div class="bar"><div class="bar-fill" style="width:%s%%"></div>'
           '<span class="bar-label">%s%% of slots reused</span></div>') % (pct, pct)
    return (
        '<h2>SteelReuse match results</h2>'
        '<div>'
        '<span class="kpi"><b>%s</b> / %s slots reused</span>'
        '<span class="kpi"><b>%s</b> kg CO2e saved</span>'
        '<span class="kpi">objective: <b>%s</b></span>'
        '<span class="kpi">%s</span>'
        '%s'
        '</div>'
        '%s'
    ) % (reused, slots,
         _num(kpis.get("co2_saved_kg"), "%.0f"), _esc(kpis.get("objective", "?")), badge, extra, bar)


def _filters():
    return (
        '<div class="filters">'
        '<label>Section <input id="srx-filter-section" oninput="srxFilter()" '
        'placeholder="e.g. W18"></label>'
        '<label>Status <select id="srx-filter-status" onchange="srxFilter()">'
        '<option value="">all</option><option value="OK">OK</option>'
        '<option value="REVIEW">review</option>'
        '<option value="CONN">connection review</option>'
        '<option value="ALT">contention (next-best used elsewhere)</option></select></label>'
        '<label>Min utilisation <input id="srx-filter-util" type="number" step="0.05" '
        'min="0" max="1" value="0" oninput="srxFilter()" style="width:5em"></label>'
        '<button type="button" class="srx-btn" onclick="srxCsv()">Copy table as CSV</button>'
        '<span id="srx-csv-note" class="copied"></span>'
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


def _util_cell(r):
    """Utilisation, colour-banded by severity (low / ok / high / over 1.0)."""
    u = r.get("utilization")
    if u is None:
        return "—"
    if u > 1.0:
        cls = "u-over"
    elif u >= 0.85:
        cls = "u-high"
    elif u >= 0.5:
        cls = "u-ok"
    else:
        cls = "u-low"
    return '<span class="%s">%.2f</span>' % (cls, u)


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
    # Headers are click-to-sort (srxSort); the body rows stay filterable (srxFilter).
    cols = ("Demand id", "Demand", "Donor id", "Donor", "Util", "Status",
            "&chi;LT", "Conn", "CO2e kg", "Next best")
    head = ('<h3>Assignments</h3>'
            '<p class="note">Click a column header to sort; use the filters above to narrow rows.</p>'
            '<table><thead><tr>'
            + "".join('<th class="srx-sort" onclick="srxSort(this)">%s</th>' % h for h in cols)
            + '</tr></thead><tbody id="srx-rows">')
    body = []
    for r in rows:
        data_section = (_esc(r.get("demand_section", "")) + " " + _esc(r.get("donor_section", ""))).upper()
        conn = "1" if r.get("connection_review") else "0"
        conn_cell = '<span class="review">review</span>' if r.get("connection_review") else ""
        alt = "1" if r.get("alt_used_elsewhere") else "0"   # contention: runner-up went elsewhere
        body.append(
            ('<tr class="srx-row" data-section="%s" data-status="%s" data-conn="%s" data-alt="%s" '
             'data-util="%s">'
             '<td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
             '<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>')
            % (data_section, _esc(r.get("check_status", "")), conn, alt, r.get("utilization", 0),
               _esc(r.get("demand_id", "")), _esc(r.get("demand_section", "")),
               _esc(r.get("donor_id", "")), _esc(r.get("donor_section", "")),
               _util_cell(r), _esc(r.get("check_status", "")),
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
    if not unfilled:
        return ('<h3>Unfilled demand slots</h3>'
                '<p class="note ok-note">None — every demand slot that could be filled was filled.</p>')
    rows = ["<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_esc(u.get("demand_id", "")), _esc(u.get("demand_section", "")),
               _esc(u.get("reason_detail", "")))
            for u in unfilled]
    summary = '<p class="note"><b>%s</b> slot(s) need new steel.</p>' % len(unfilled)
    return summary + _simple_table("Unfilled demand slots (need new steel)",
                                   ["Demand id", "Section", "Why unfilled"], rows)


def _rollup_section(assignments):
    """Reuse rolled up by donor section: count, total CO2e saved, mean utilisation, total off-cut.

    A quick read of where the savings concentrate and how hard each section is worked, without
    scanning the per-row table."""
    if not assignments:
        return ""
    groups = {}
    for a in assignments:
        sec = a.get("donor_section") or "(unmapped)"
        g = groups.get(sec)
        if g is None:
            g = groups[sec] = {"n": 0, "co2": 0.0, "offcut": 0.0, "util_sum": 0.0, "util_n": 0}
        g["n"] += 1
        g["co2"] += a.get("co2_saved_kg") or 0.0
        g["offcut"] += a.get("offcut_mm") or 0.0
        u = a.get("utilization")
        if u is not None:
            g["util_sum"] += u
            g["util_n"] += 1
    rows = []
    for sec in sorted(groups, key=lambda s: -groups[s]["co2"]):
        g = groups[sec]
        mean_u = (g["util_sum"] / g["util_n"]) if g["util_n"] else None
        rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                    % (_esc(sec), g["n"], _num(g["co2"], "%.0f"),
                       _num(mean_u, "%.2f"), _num(g["offcut"] / 1000.0, "%.1f")))
    return _simple_table("Reuse by donor section",
                         ["Donor section", "Reuses", "CO2e saved (kg)", "Mean util", "Off-cut (m)"],
                         rows)


def _diagnosis_section(diagnosis):
    """The 'why' box: the binding constraint on reuse and the lever that would improve it."""
    if not diagnosis:
        return ""
    binding = diagnosis.get("binding_constraint")
    if not binding or binding == "none":
        return ""
    out = ('<div class="diag"><b>Why %s slot(s) went unfilled:</b> the binding constraint is '
           '<b>%s</b> &mdash; %s.') % (
        diagnosis.get("n_unmatched", 0), _esc(binding), _esc(diagnosis.get("lever", "")))
    ex = diagnosis.get("overspec_example")
    if diagnosis.get("n_overspec", 0) and ex:
        out += (' <span class="note">%s reused member(s) are well over-spec (e.g. %s where %s would '
                'pass) &mdash; honest under avoided-new, but a stewardship flag.</span>') % (
            diagnosis.get("n_overspec"), _esc(ex.get("donor", "")), _esc(ex.get("lighter", "")))
    return out + '</div>'


def _warnings_section(warnings):
    """Engineering flags the reviewer should eyeball before trusting the match."""
    if not warnings:
        return ""
    items = [
        ("LTB restraint-reliant beams", warnings.get("ltb_restraint_reliant", 0)),
        ("Imperfection-governed members", warnings.get("imperfection_governed", 0)),
        ("Connection-review flags", warnings.get("connection_review", 0)),
        ("Donors cut to length", warnings.get("cut_donors", 0)),
        ("Unidentified donor members", warnings.get("unknown", 0)),
    ]
    rows = ['<tr><td>%s</td><td>%s</td></tr>' % (_esc(k), v) for k, v in items]
    if warnings.get("cut_donors"):
        rows.append('<tr><td>Reusable remainder (m)</td><td>%s</td></tr>'
                    % _num(warnings.get("reusable_remainder_m"), "%.1f"))
    out = _simple_table("Warnings & flags", ["Check", "Count"], rows)
    breakdown = warnings.get("unknown_breakdown") or []
    if breakdown:
        brows = ['<tr><td>%s</td><td>%s</td></tr>' % (_esc(b.get("name", "")), b.get("count", ""))
                 for b in breakdown[:10]]
        out += _simple_table("Unidentified donor types (top 10)", ["Raw name", "Count"], brows)
    return out


def _disposition_section(disp):
    """Stock disposition: what to do with each UNUSED donor (store / re-roll / recycle), with reasons."""
    if not disp:
        return ""
    t = disp.get("totals", {})
    head = ('<h3>Stock disposition (unused donors)</h3>'
            '<p class="note"><b>%s</b> unused donor(s): <b>%s</b> store / <b>%s</b> re-roll / '
            '<b>%s</b> recycle. Potential credits: %s kg CO2e re-roll, %s kg CO2e recycle.') % (
        t.get("n", "?"), t.get("store", 0), t.get("reroll", 0), t.get("recycle", 0),
        _num(t.get("reroll_credit_kg"), "%.1f"), _num(t.get("recycle_credit_kg"), "%.1f"))
    br = t.get("by_reason") or {}
    if br:
        head += (' Why unused: %s too-short, %s too-weak, %s contention, %s uneconomic.') % (
            br.get("too-short", 0), br.get("too-weak", 0), br.get("contention", 0),
            br.get("uneconomic", 0))
    head += '</p>'
    rows = ['<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (_esc(r.get("section", "")), r.get("n", ""), r.get("store", ""),
               r.get("reroll", ""), r.get("recycle", ""))
            for r in disp.get("by_section", [])]
    return head + _simple_table("", ["Section", "Donors", "Store", "Re-roll", "Recycle"], rows)


def _marginal_section(rows):
    """Tier 4 'donor what-if value': each reused donor's true worth, from a re-solve without it."""
    if not rows:
        return ""
    srt = sorted(rows, key=lambda r: -(r.get("marginal_co2_kg") or 0))
    body = []
    for r in srt:
        lost = ", ".join(r.get("slots_lost") or []) or "—"
        body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                    % (_esc(r.get("supply_id", "")), _esc(r.get("section", "")),
                       _num(r.get("marginal_co2_kg"), "%.1f"), _esc(lost),
                       r.get("reshuffled_slots", 0)))
    head = ('<h3>Donor what-if value</h3>'
            '<p class="note">Each reused donor re-solved without it: the marginal value is the drop in '
            'total CO2e saved (its true worth to the solution). Small = a close substitute exists; '
            'large = the result leans on it.</p>')
    return head + _simple_table(
        "", ["Donor", "Section", "Marginal CO2e (kg)", "Slots lost", "Other slots reshuffled"], body)


def _pareto_section(rows):
    """Objective trade-off: the same stock solved for co2 / members / mass."""
    if not rows:
        return ""
    body = []
    for r in rows:
        mark = "&#9733;" if r.get("selected") else ""   # star the objective this run followed
        body.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                    % (mark, _esc(r.get("label") or r.get("objective", "")), r.get("n_reused", ""),
                       _num(r.get("co2_saved_kg"), "%.1f"), _num(r.get("mass_reused_kg"), "%.1f")))
    return _simple_table("Objective trade-off (Pareto)",
                         ["", "Objective", "Reused", "CO2e saved (kg)", "Mass reused (kg)"], body)


def _portfolio_section(rows):
    """Per-project outcome when several demand models shared one donor stock."""
    if not rows:
        return ""
    body = ['<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (_esc(p.get("tag", "")), p.get("slot_count", ""), p.get("n_reused", ""),
               _num(p.get("co2_saved_kg"), "%.1f"), p.get("n_unmatched", ""))
            for p in rows]
    return _simple_table("Portfolio (projects sharing one donor stock)",
                         ["Project", "Slots", "Reused", "CO2e saved (kg)", "Need new steel"], body)


def _audit_section(audit):
    """Pre-demolition-audit provenance summary + the quarantine list, when the donor carried audit data."""
    if not audit:
        return ""
    head = ('<h3>Pre-demolition audit</h3>'
            '<p class="note"><b>%s</b> audited, <b>%s</b> admitted, <b>%s</b> quarantined; '
            'average f_y knockdown %s.</p>') % (
        audit.get("audited", "?"), audit.get("admitted", "?"), audit.get("quarantined", "?"),
        _num(audit.get("avg_knockdown"), "%.2f"))
    ql = audit.get("quarantined_list") or []
    rows = ['<tr><td>%s</td><td>%s</td></tr>' % (_esc(q.get("id", "")), _esc(q.get("reason", "")))
            for q in ql]
    return head + (_simple_table("", ["Quarantined donor", "Reason"], rows) if rows else "")


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
    """results.json dict -> a self-contained HTML string for ``output.print_html``.

    Sections render in review order; the optional ones (disposition, donor what-if value, pareto,
    portfolio, audit) appear only when that analysis ran, so a plain run stays compact.
    """
    kpis = data.get("kpis", {})
    parts = [_STYLE, '<div class="srx">',
             _kpi_header(kpis),
             _diagnosis_section(data.get("diagnosis")),
             _rules_line(data.get("rules")),
             _filters(),
             _assignments_table(data.get("assignments", [])),
             _rollup_section(data.get("assignments", [])),
             _unfilled_section(data.get("unfilled", [])),
             _warnings_section(data.get("warnings")),
             _disposition_section(data.get("disposition")),
             _marginal_section(data.get("marginal_value")),
             _pareto_section(data.get("pareto")),
             _portfolio_section(data.get("portfolio")),
             _quarantine_section(data.get("quarantined_donors", [])),
             _mismatch_section(data.get("mismatch")),
             _audit_section(data.get("audit")),
             '</div>', _FILTER_JS]
    return "\n".join(parts)
