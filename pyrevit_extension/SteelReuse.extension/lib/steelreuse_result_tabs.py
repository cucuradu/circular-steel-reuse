# -*- coding: utf-8 -*-
"""Shared text formatters for the result-review tabs.

The Run Match window and the saved-runs Results window show the SAME review tabs (unfilled +
diagnosis, by-section roll-up, warnings, disposition, donor what-if value, pareto, portfolio, audit,
donor provenance). Each tab body is a monospace text table built purely from a parsed
:class:`steelreuse_panel_model.ResultsView`, so both windows render identically and the logic is
unit-testable here under CPython exactly as it runs under IronPython in Revit.

Pure + stdlib only. ``has_*(view)`` say whether an optional block is present, so each window can hide
the tabs whose data a given run did not produce.
"""

import steelreuse_panel_model as panelmodel


def diagnosis(view):
    """One-line 'why the rest went unfilled': the binding constraint + lever (or an all-filled note)."""
    diag = view.diagnosis or {}
    binding = diag.get("binding_constraint")
    if binding and binding != "none":
        return "Binding constraint: %s.  %s" % (binding, diag.get("lever", ""))
    return "Every demand slot that could be filled was filled."


def unfilled(view):
    """Per-slot list of the demand that needs new steel, each with its element-specific reason."""
    rows = view.unfilled
    out = ["%s unfilled slot(s) need new steel:" % len(rows), ""]
    for r in rows:
        out.append("  %-16s %-12s %s"
                   % (r.get("slot_id", ""), r.get("demand_section", ""), r.get("reason_detail", "")))
    return "\n".join(out)


def rollup(view):
    """Reuse rolled up by donor section: count / CO2e saved / mean utilisation / off-cut."""
    out = ["%-14s %7s %14s %11s %10s"
           % ("section", "reuses", "CO2e (kg)", "mean util", "off-cut m"), ""]
    for g in panelmodel.section_rollup(view.rows):
        out.append("%-14s %7s %14.0f %11.2f %10.1f"
                   % (g["section"], g["n"], g["co2"], g["mean_util"], g["offcut_m"]))
    return "\n".join(out)


def warnings(view):
    """Engineering flags to eyeball before trusting the match, plus the unidentified-donor breakdown."""
    w = view.warnings or {}
    out = [
        "LTB restraint-reliant beams : %s" % w.get("ltb_restraint_reliant", 0),
        "Imperfection-governed       : %s" % w.get("imperfection_governed", 0),
        "Donors cut to length        : %s   (remainder %s m)"
        % (w.get("cut_donors", 0), w.get("reusable_remainder_m", 0)),
        "Connection-review flags     : %s" % w.get("connection_review", 0),
        "Unidentified donor members  : %s" % w.get("unknown", 0),
    ]
    breakdown = w.get("unknown_breakdown", [])
    if breakdown:
        out += ["", "Unidentified breakdown (top 20):"]
        for b in breakdown[:20]:
            out.append("  %6s x %s" % (b.get("count", ""), b.get("name", "")))
    return "\n".join(out)


def pareto(view):
    """Objective trade-off: the same stock solved for co2 / members / mass, one row per goal."""
    out = ["%-10s %8s %14s %14s  %s"
           % ("objective", "reused", "CO2e (kg)", "mass (kg)", "optimal"), ""]
    for p in view.pareto:
        mark = "*" if p.get("selected") else " "
        out.append("%s%-9s %8s %14.1f %14.1f  %s"
                   % (mark, p.get("label") or p.get("objective", ""), p.get("n_reused", ""),
                      float(p.get("co2_saved_kg") or 0), float(p.get("mass_reused_kg") or 0),
                      "yes" if p.get("proven_optimal") else "no"))
    out += ["", "* = the objective this run's assignments follow."]
    return "\n".join(out)


def disposition(view):
    """Per-section disposition of the UNUSED donors (store / re-roll / recycle) + why-unused counts."""
    d = view.disposition or {}
    t = d.get("totals", {})
    br = t.get("by_reason", {})
    out = [
        "Unused donors: %s    store %s | re-roll %s | recycle %s"
        % (t.get("n", ""), t.get("store", ""), t.get("reroll", ""), t.get("recycle", "")),
        "Potential credits: %.1f kg CO2e re-roll, %.1f kg CO2e recycle"
        % (float(t.get("reroll_credit_kg") or 0), float(t.get("recycle_credit_kg") or 0)),
    ]
    if br:
        out.append(
            "Why unused: too-short %s | too-weak %s | contention %s | uneconomic %s"
            % (br.get("too-short", 0), br.get("too-weak", 0),
               br.get("contention", 0), br.get("uneconomic", 0)))
    out += [
        "",
        "%-12s %7s %6s %8s %8s" % ("section", "donors", "store", "re-roll", "recycle"), "",
    ]
    for r in d.get("by_section", []):
        out.append("%-12s %7s %6s %8s %8s"
                   % (r.get("section", ""), r.get("n", ""), r.get("store", ""),
                      r.get("reroll", ""), r.get("recycle", "")))
    return "\n".join(out)


def marginal(view):
    """Donor what-if value: each reused donor's worth, from a re-solve with it removed (Tier 4)."""
    rows = sorted(view.marginal_value, key=lambda r: -(r.get("marginal_co2_kg") or 0))
    out = [
        "What each reused donor is worth to the solution (re-solved without it):",
        "A small value = a close substitute exists; a large value = the result leans on it.",
        "",
        "%-10s %-12s %14s %12s %8s" % ("donor", "section", "marginal CO2e", "slots lost", "reshuf."),
        "",
    ]
    for r in rows:
        lost = ", ".join(r.get("slots_lost") or []) or "-"
        out.append("%-10s %-12s %14.1f %12s %8s"
                   % (r.get("supply_id", ""), r.get("section", ""),
                      float(r.get("marginal_co2_kg") or 0), lost, r.get("reshuffled_slots", 0)))
    return "\n".join(out)


def portfolio(view):
    """Per-project outcome when several demand models shared one donor stock."""
    out = ["%-18s %6s %7s %14s %9s"
           % ("project", "slots", "reused", "CO2e (kg)", "unfilled"), ""]
    for p in view.portfolio:
        out.append("%-18s %6s %7s %14.1f %9s"
                   % (p.get("tag", ""), p.get("slot_count", ""), p.get("n_reused", ""),
                      float(p.get("co2_saved_kg") or 0), p.get("n_unmatched", "")))
    return "\n".join(out)


def audit(view):
    """Pre-demolition-audit provenance: verification basis, condition grades, and quarantine list."""
    a = view.audit or {}
    out = ["Audited %s | admitted %s | quarantined %s | avg knockdown %s"
           % (a.get("audited", ""), a.get("admitted", ""), a.get("quarantined", ""),
              a.get("avg_knockdown", "")), "", "Verification basis:"]
    for vb in a.get("verification", []):
        out.append("  %-14s %s" % (vb.get("basis", ""), vb.get("count", "")))
    out += ["", "Condition grade:"]
    for c in a.get("condition", []):
        out.append("  %-14s %s" % (c.get("grade", ""), c.get("count", "")))
    quarantined = a.get("quarantined_list", [])
    if quarantined:
        out += ["", "Quarantined (%s):" % len(quarantined)]
        for q in quarantined:
            out.append("  %-14s %s" % (q.get("id", ""), q.get("reason", "")))
    return "\n".join(out)


def provenance(view):
    """Rule-data versions + the donor mismatch log (Roadmap 1.2): every donor classified with a reason."""
    rules = view.rules or {}
    mismatch = view.mismatch or {}
    summary = mismatch.get("summary", {})
    rows = mismatch.get("rows", [])
    out = []
    if rules.get("ruleset_version"):
        tables = ", ".join("%s v%s" % (t.get("name", ""), t.get("version", ""))
                           for t in rules.get("tables", []))
        out += ["Rule data (externalised + versioned):",
                "  ruleset v%s" % rules.get("ruleset_version"),
                "  tables: %s" % tables,
                "  carbon factors: v%s" % rules.get("carbon_factors_version", "?"), ""]
    cover = "100%" if summary.get("accounts_for_all") else "INCOMPLETE"
    out += ["Donor provenance -- %s of %s donor row(s) accounted for:"
            % (cover, summary.get("n_donor_rows", "?")),
            "  %s mapped | %s fuzzy | %s unknown | %s quarantined"
            % (summary.get("mapped", 0), summary.get("fuzzy", 0),
               summary.get("unknown", 0), summary.get("quarantined", 0)), ""]
    out.append("%-14s %-11s %-9s %s" % ("donor id", "class", "outcome", "reason"))
    for r in rows:
        out.append("%-14s %-11s %-9s %s"
                   % (r.get("id", ""), r.get("classification", ""),
                      r.get("outcome", ""), r.get("reason", "")))
    return "\n".join(out)
