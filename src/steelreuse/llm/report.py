"""Report builder: deterministic numbers + (optional) LLM narrative.

Hard rule (docs/DESIGN_PRINCIPLES.md): numbers are computed in Python and injected by Jinja2. The LLM only writes
prose; its output is screened by :func:`find_invented_numbers` and discarded (deterministic fallback)
if it introduces any figure not present in the computed results.
"""

from __future__ import annotations

import re
from collections import Counter

from ..pipeline import PipelineResult
from .providers import LLMProvider, NullProvider

SCOPE_DISCLAIMER = (
    "Member-level pre-feasibility screening only. Excludes connection design; results are "
    "decision-support, not code-certified. Reused steel requires physical verification "
    "(coupon testing, corrosion/fatigue survey) and connection design by an engineer."
)

# Human wording for the matcher's optimization goal (kept number-free for the LLM guard).
_OBJECTIVE_LABEL = {"co2": "net-CO2", "members": "members-reused", "mass": "reclaimed-mass"}


def build_report_context(res: PipelineResult) -> dict:
    """Flatten a :class:`PipelineResult` into a JSON-ish dict of pre-computed values for the template."""
    m = res.match
    p = res.passport
    decisions = res.audit.decisions if res.audit else {}
    assignments = []
    for a in m.assignments:
        d = decisions.get(a.supply_id)
        assignments.append({
            "slot": a.slot_id, "supply": a.supply_id, "section": a.section,
            "utilization": a.utilization, "status": a.status,
            "offcut_mm": a.offcut_mm, "co2_saved_kg": a.co2_saved_kg,
            "chi_lt": a.chi_lt, "chi_lt_if_free": a.chi_lt_if_free,
            "governing": a.governing_combination,
            # Pre-demolition-audit provenance for the reclaimed member used in this assignment.
            "verification": (d.verification or "—") if (d and d.audited) else "—",
            "condition": (d.condition.upper() or "—") if (d and d.audited and d.condition) else "—",
            "knockdown": round(d.knockdown, 3) if d else 1.0,
            "connection": a.connection_status,
            "connection_note": a.connection_note,
        })
    # How many reuses are governed by a non-gravity combination (e.g. the sway-imperfection case),
    # so the report can note that the load-combination envelope, not just gravity, sized the member.
    n_imperfection_governed = sum(
        1 for a in m.assignments if a.governing_combination != "ULS gravity"
    )
    # Beams that pass only because the slab restrains the compression flange: chi_LT would drop below
    # 0.85 if unrestrained. Surfacing this makes the LTB check visible and flags construction-stage risk.
    ltb_restraint_reliant = sum(
        1 for a in m.assignments
        if a.chi_lt == 1.0 and a.chi_lt_if_free is not None and a.chi_lt_if_free < 0.85
    )
    # Summarize unknowns by distinct raw name + count, so a model with hundreds of identical
    # non-steel members (bar joists, concrete) yields a short table instead of a wall of text.
    unknown_counts = Counter(u.raw for u in res.validation.unknown)
    unknown_breakdown = [
        {"name": name, "count": n} for name, n in unknown_counts.most_common()
    ]
    ctx = {
        "supply_count": res.supply_count,
        "slot_count": res.slot_count,
        "mapped": len(res.validation.mapped),
        "fuzzy": len(res.validation.fuzzy),
        "unknown": len(res.validation.unknown),
        "unknown_kinds": len(unknown_breakdown),
        "unknown_breakdown": unknown_breakdown,
        "total_mass_kg": round(p.total_mass_kg, 1),
        "total_new_co2_kg": round(p.total_new_kgco2e, 1),
        "donor_saved_co2_kg": round(p.total_saved_kgco2e, 1),
        "n_reused": m.n_reused,
        "reuse_rate_pct": round(100.0 * m.n_reused / res.slot_count) if res.slot_count else 0,
        "match_co2_saved_kg": round(m.total_co2_saved_kg, 1),
        "total_offcut_mm": round(m.total_offcut_mm, 1),
        "n_unmatched": len(m.unmatched_slots),
        "n_unused": len(m.unused_supply),
        "unmatched_slots": m.unmatched_slots,
        "unused_supply": m.unused_supply,
        "solver_status": m.solver_status,
        # Human-readable optimality claim for the footer: a proven-optimal MILP result is the best
        # possible assignment for the stated objective; the greedy fallback is feasible but unproven.
        "match_optimality": (
            f"matching proven optimal (MILP, "
            f"{_OBJECTIVE_LABEL.get(m.objective, m.objective)} objective)"
            if m.proven_optimal else
            f"matching heuristic ({_OBJECTIVE_LABEL.get(m.objective, m.objective)} objective) "
            f"— not proven optimal"),
        "assignments": assignments,
        "ltb_restraint_reliant": ltb_restraint_reliant,
        "n_imperfection_governed": n_imperfection_governed,
        # Geometric connection-compatibility screen (core/connections.py): how many reuses need a
        # connection look before they are practical. "unknown" (no design section) is not counted.
        "connection_review": sum(1 for a in m.assignments if a.connection_status == "review"),
        "connection_screen_on": bool(m.weights.get("connection_screen")),
        "cut_donors": len(m.donor_leftover_mm),
        "reusable_remainder_m": round(m.total_donor_leftover_mm / 1000.0, 1),
        # End-of-life counterfactual basis the savings were booked on ("none" = plain avoided-new).
        "counterfactual": (m.weights or {}).get("counterfactual", "none"),
        # Section variety of the result (anti-Frankenstein metric, always shown; cap when set).
        "distinct_sections": len({a.section for a in m.assignments}),
        "max_distinct_sections": (m.weights or {}).get("max_distinct_sections"),
        # WHY the reuse came out this way: the binding constraint among unfilled slots + the lever
        # (steelreuse.match.optimize.diagnose_match). The narrative explains this; numbers stay here.
        "diagnosis": res.diagnosis or {},
        "disclaimer": SCOPE_DISCLAIMER,
    }
    # Portfolio (C1): per-project breakdown of the combined allocation across demand models.
    if res.projects:
        ctx["projects"] = [
            {"tag": p["tag"], "slot_count": p["slot_count"], "n_reused": p["n_reused"],
             "co2_saved_kg": p["co2_saved_kg"], "n_unmatched": p["n_unmatched"]}
            for p in res.projects
        ]
    # Objective trade-off rows (only when run_pipeline(pareto=True) re-solved every goal).
    if res.pareto:
        ctx["pareto"] = [
            dict(p, label=_OBJECTIVE_LABEL.get(p["objective"], p["objective"]))
            for p in res.pareto
        ]
    # Stock disposition advisory (only when run_pipeline(disposition=True) computed it):
    # summarized BY SECTION so a 300-donor stock yields a short table, not 300 rows.
    if res.disposition is not None:
        by_sec: dict[str, dict] = {}
        for r in res.disposition:
            row = by_sec.setdefault(r["section"], {
                "section": r["section"], "n": 0, "store": 0, "reroll": 0, "recycle": 0,
                "reroll_credit_kg": 0.0, "recycle_credit_kg": 0.0,
            })
            row["n"] += 1
            key = {"store": "store", "re-roll": "reroll", "recycle": "recycle"}[r["advice"]]
            row[key] += 1
            row["reroll_credit_kg"] += r["reroll_credit_kg"]
            row["recycle_credit_kg"] += r["recycle_credit_kg"]
        for row in by_sec.values():
            row["reroll_credit_kg"] = round(row["reroll_credit_kg"], 1)
            row["recycle_credit_kg"] = round(row["recycle_credit_kg"], 1)
        ctx["disposition_present"] = True
        ctx["disposition_by_section"] = sorted(by_sec.values(), key=lambda r: -r["n"])
        ctx["disposition_totals"] = {
            "n": len(res.disposition),
            "store": sum(1 for r in res.disposition if r["advice"] == "store"),
            "reroll": sum(1 for r in res.disposition if r["advice"] == "re-roll"),
            "recycle": sum(1 for r in res.disposition if r["advice"] == "recycle"),
            "reroll_credit_kg": round(sum(r["reroll_credit_kg"] for r in res.disposition), 1),
            "recycle_credit_kg": round(sum(r["recycle_credit_kg"] for r in res.disposition), 1),
        }
    else:
        ctx["disposition_present"] = False
    # Pre-demolition-audit provenance summary (only shown when the donor model carried audit data).
    if res.audit and res.audit.present:
        a = res.audit
        ctx["audit_present"] = True
        ctx["audit_audited"] = a.n_audited
        ctx["audit_admitted"] = a.n_admitted
        ctx["audit_quarantined"] = a.n_quarantined
        ctx["audit_avg_knockdown"] = a.avg_knockdown
        ctx["audit_verification"] = [
            {"basis": k, "count": v} for k, v in sorted(a.verification_counts.items())
        ]
        ctx["audit_condition"] = [
            {"grade": k, "count": v} for k, v in sorted(a.condition_counts.items())
        ]
        ctx["audit_quarantined_list"] = [
            {"id": mid, "reason": reason} for mid, reason in a.quarantined
        ]
    else:
        ctx["audit_present"] = False
    return ctx


def _allowed_numbers(ctx: dict) -> set[float]:
    """All numeric values we present, so the LLM guard can detect invented figures."""
    vals: set[float] = set()

    def walk(o):
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            vals.add(round(float(o), 2))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(ctx)
    return vals


def find_invented_numbers(text: str, allowed: set[float], tol: float = 0.5) -> list[float]:
    """Return numbers in ``text`` that don't match any allowed value (within ``tol``)."""
    invented: list[float] = []
    # word boundaries so digits inside identifiers (e.g. "W12x40", "IPE300") are not treated as figures
    for tok in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?!\w)", text):
        v = float(tok)
        if not any(abs(v - a) <= tol for a in allowed):
            invented.append(v)
    return invented


def deterministic_narrative(ctx: dict) -> str:
    """A safe, number-faithful summary written purely from the computed context.

    Leads with the *diagnosis* (why the result came out this way and the lever to improve it), not a
    recital of counts — the same conclusion the LLM is asked to render, available with no provider.
    """
    rate = ctx.get("reuse_rate_pct")
    parts = [
        f"{ctx['n_reused']} of {ctx['slot_count']} demand slot(s) were matched to reclaimed steel"
        + (f" ({rate}%)" if rate is not None else "")
        + f", avoiding about {ctx['match_co2_saved_kg']} kg CO2e of new-steel production."
    ]
    d = ctx.get("diagnosis") or {}
    binding = d.get("binding_constraint")
    if ctx["n_unmatched"] and binding and binding != "none":
        why = {
            "length": (f"the {d['n_unmatched']} unfilled slot(s) need longer members than the free "
                       "stock provides — the donors both strong enough and long enough are used up "
                       "and the remainder is too short for these spans"),
            "capacity": (f"{d['capacity_limited']} of the {d['n_unmatched']} unfilled slot(s) had no "
                         "donor section strong or stiff enough"),
            "contention": (f"the adequate stock ran out — {d['contention']} unfilled slot(s) would "
                           "have taken a donor had more of the right sections been available"),
            "economics": (f"{d['uneconomic']} unfilled slot(s) could only be filled by donors so "
                          "over-spec that reuse would book negative net CO2"),
        }.get(binding, "")
        parts.append(f"The binding constraint is {binding}: {why}. In short, {d['lever']}.")
    # Over-spec ("upgrade") matches — flagged only when several reuses are markedly heavier than the
    # lightest section that would pass (the "Frankenstein receiver"); honest, but a stewardship signal.
    ex = d.get("overspec_example")
    if d.get("n_overspec", 0) >= 3 and ex:
        parts.append(f"{d['n_overspec']} reused member(s) are well over-spec — a much lighter section "
                     f"would have passed (e.g. {ex['donor']} where {ex['lighter']} suffices). That is "
                     "honest under avoided-new (booked at the lighter section's carbon), but "
                     "--w-overspec or --reserve would steer such heavy stock toward the slots that "
                     "actually need it.")
    if ctx.get("ltb_restraint_reliant"):
        parts.append(f"{ctx['ltb_restraint_reliant']} reused beam(s) pass bending only because the "
                     "slab restrains the compression flange — confirm that restraint, especially at "
                     "the construction stage.")
    if ctx.get("cut_donors"):
        parts.append(f"{ctx['cut_donors']} donor(s) were cut to length, leaving "
                     f"{ctx['reusable_remainder_m']} m of reusable remainder for other work.")
    if ctx["unknown"]:
        top = "; ".join(f"{b['count']}x {b['name']}" for b in ctx["unknown_breakdown"][:3])
        parts.append(f"{ctx['unknown']} donor member(s) across {ctx['unknown_kinds']} type(s) could "
                     f"not be identified and were excluded (top: {top}).")
    if ctx.get("audit_present"):
        parts.append(f"A pre-demolition audit covered {ctx['audit_audited']} donor member(s) "
                     f"(average f_y knockdown {ctx['audit_avg_knockdown']}); "
                     f"{ctx['audit_quarantined']} were quarantined as unverified or unsuitable.")
    return " ".join(parts)


def generate_narrative(ctx: dict, provider: LLMProvider | None = None) -> tuple[str, str]:
    """Return (narrative, source). Falls back to the deterministic text if no/invalid LLM output."""
    provider = provider or NullProvider()
    if isinstance(provider, NullProvider):
        return deterministic_narrative(ctx), "deterministic"

    system = (
        "You are a structural-reuse analyst writing the summary of a steel-reuse matching report for "
        "an engineer. Be ANALYTICAL, not a list. Lead with the headline outcome, then EXPLAIN WHY the "
        "result came out this way: the binding constraint and the lever that would improve it are "
        "given to you in the 'diagnosis' field — build the explanation around them in your own words "
        "(e.g. 'reuse here is limited by donor length, not capacity, so cutting or splicing is the "
        "lever'). Then flag the key risks (beams that rely on slab restraint for LTB, quarantined or "
        "unidentified stock). Do NOT recite the assignment table or list section names. CRITICAL: do "
        "no arithmetic and introduce no number that is not already in the data. Keep it under 130 words."
    )
    prompt = (
        "Computed results — explain them in plain language, do not change or invent any number:\n"
        f"- headline: {ctx['n_reused']} of {ctx['slot_count']} slots reused "
        f"({ctx.get('reuse_rate_pct')}%), {ctx['match_co2_saved_kg']} kg CO2e avoided\n"
        f"- diagnosis (why, and the lever): {ctx.get('diagnosis')}\n"
        f"- risks: beams relying on slab restraint for LTB = {ctx.get('ltb_restraint_reliant')}, "
        f"unidentified donors = {ctx['unknown']}, donors cut to length = {ctx['cut_donors']}\n"
        f"- over-spec/upgrade matches (heavy donor where a much lighter section would pass, honest but "
        f"a stewardship flag): {ctx.get('diagnosis', {}).get('n_overspec')} "
        f"(example: {ctx.get('diagnosis', {}).get('overspec_example')})\n"
        f"- full computed context (numbers you may quote): {ctx}"
    )
    try:  # pragma: no cover - exercised only with a live provider
        text = provider.complete(system, prompt).strip()
    except Exception:
        return deterministic_narrative(ctx), "deterministic (provider error)"

    if not text or find_invented_numbers(text, _allowed_numbers(ctx)):
        return deterministic_narrative(ctx), f"deterministic (rejected {provider.name} output)"
    return text, provider.name


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Circular Steel Reuse Report</title>
<style>
 body{font-family:system-ui,Arial,sans-serif;margin:2rem;color:#1a1a1a;max-width:900px}
 h1{font-size:1.5rem} .kpis{display:flex;gap:1.5rem;flex-wrap:wrap;margin:1rem 0}
 .kpi{background:#f3f6f4;border-radius:10px;padding:1rem 1.25rem}
 .kpi b{display:block;font-size:1.6rem} table{border-collapse:collapse;width:100%;margin:1rem 0}
 th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;font-size:.9rem}
 th{background:#eef2ef} .review{color:#a15c00} .warn{background:#fff7e6;padding:.75rem;border-radius:8px}
 .disc{color:#555;font-size:.85rem;border-left:3px solid #bbb;padding-left:.8rem;margin-top:1.5rem}
 .note{color:#555;font-size:.9rem}
</style></head><body>
<h1>Circular Structural Reuse — Matching Report</h1>
<p><em>{{ narrative }}</em> <span style="color:#888">(narrative: {{ narrative_source }})</span></p>
<div class="kpis">
 <div class="kpi"><b>{{ ctx.n_reused }}</b>members reused</div>
 <div class="kpi"><b>{{ ctx.match_co2_saved_kg }}</b>kg CO2e saved</div>
 <div class="kpi"><b>{{ ctx.donor_saved_co2_kg }}</b>kg CO2e in full donor stock</div>
 <div class="kpi"><b>{{ ctx.unmatched_slots|length }}</b>slots need new steel</div>
</div>
{% if ctx.diagnosis and ctx.diagnosis.binding_constraint and ctx.diagnosis.binding_constraint != 'none' %}
<p class="note"><b>Why the rest went unfilled:</b> the binding constraint is
<b>{{ ctx.diagnosis.binding_constraint }}</b> — {{ ctx.diagnosis.lever }}.</p>{% endif %}
{% if ctx.projects %}<h2>Portfolio — projects sharing one donor stock</h2>
<p class="note">One optimization allocated the donor stock across all the projects below at once —
a donor goes wherever it saves the most, so "save the heavy sections for the project that needs
them" is an optimization outcome, not a hunch. Slot ids are prefixed with the project tag.</p>
<table><tr><th>Project</th><th>Slots</th><th>Reused</th><th>CO2e saved (kg)</th>
<th>Slots needing new steel</th></tr>
{% for p in ctx.projects %}<tr>
 <td>{{ p.tag }}</td><td>{{ p.slot_count }}</td><td>{{ p.n_reused }}</td>
 <td>{{ p.co2_saved_kg }}</td><td>{{ p.n_unmatched }}</td></tr>{% endfor %}
</table>{% endif %}
<h2>Assignments</h2>
<table><tr><th>Demand slot</th><th>Reclaimed member</th><th>Section</th><th>Utilization</th>
<th>Gov. load case</th><th>Status</th><th>&chi;<sub>LT</sub></th><th>Connection</th>
{% if ctx.audit_present %}<th>Provenance</th>{% endif %}<th>Off-cut (mm)</th>
<th>CO2e saved (kg)</th></tr>
{% for a in ctx.assignments %}<tr>
 <td>{{ a.slot }}</td><td>{{ a.supply }}</td><td>{{ a.section }}</td>
 <td>{{ '%.2f'|format(a.utilization) }}</td>
 <td>{{ a.governing }}</td>
 <td class="{{ 'review' if a.status=='REVIEW' else '' }}">{{ a.status }}</td>
 <td>{% if a.chi_lt is none %}—{% else %}{{ '%.2f'|format(a.chi_lt) }}{% if a.chi_lt == 1.0 and a.chi_lt_if_free is not none and a.chi_lt_if_free < 0.85 %} <span class="review" title="would be {{ '%.2f'|format(a.chi_lt_if_free) }} if the flange were unrestrained">⚠</span>{% endif %}{% endif %}</td>
 <td>{% if a.connection == 'review' %}<span class="review" title="{{ a.connection_note }}">review</span>{% elif a.connection == 'unknown' %}—{% else %}{{ a.connection }}{% endif %}</td>
 {% if ctx.audit_present %}<td>{{ a.verification }}{% if a.condition != '—' %} / cond {{ a.condition }}{% endif %}{% if a.knockdown < 1.0 %} / k={{ '%.2f'|format(a.knockdown) }}{% endif %}</td>{% endif %}
 <td>{{ a.offcut_mm }}</td><td>{{ a.co2_saved_kg }}</td></tr>{% endfor %}
</table>
{% if ctx.connection_review %}<p class="note">{{ ctx.connection_review }} assignment(s) are geometrically
compatible but flagged <b>connection review</b> (shallower than the design section, thinner web, or
narrower flange than the connections were detailed for — hover the cell for the reason). Connection
design itself remains outside this tool's scope.</p>{% endif %}
{% if ctx.connection_screen_on %}<p class="note">The connection feasibility screen was ON: donors
geometrically incompatible with a slot's design section (wrong shape family, or too deep for the
detailed zone) were excluded before matching.</p>{% endif %}
{% if ctx.counterfactual != 'none' %}<p class="note"><b>Carbon basis:</b> savings are booked NET of
the foregone <b>{{ ctx.counterfactual }}</b> credit — each reuse is charged for the end-of-life
benefit the consumed donor steel would have delivered anyway{% if ctx.counterfactual == 'rerolling' %}
(direct re-rolling is a pilot-scale route; its credit is a research-grade figure){% endif %}. This is
deliberately harsher than plain avoided-new accounting; reuses that no longer save carbon on this
basis were not selected.</p>{% endif %}
{% if ctx.audit_present %}<h2>Pre-demolition audit</h2>
<p>{{ ctx.audit_audited }} donor member(s) carried audit data: {{ ctx.audit_admitted }} admitted to
 supply, {{ ctx.audit_quarantined }} quarantined. Average f<sub>y</sub> knockdown on admitted members:
 {{ ctx.audit_avg_knockdown }}.</p>
<table><tr><th>Verification basis</th><th>Count</th></tr>
 {% for v in ctx.audit_verification %}<tr><td>{{ v.basis }}</td><td>{{ v.count }}</td></tr>{% endfor %}
</table>
{% if ctx.audit_condition %}<table><tr><th>Condition grade</th><th>Count</th></tr>
 {% for c in ctx.audit_condition %}<tr><td>{{ c.grade }}</td><td>{{ c.count }}</td></tr>{% endfor %}
</table>{% endif %}
{% if ctx.audit_quarantined_list %}<div class="warn">⚠ {{ ctx.audit_quarantined }} donor member(s)
 quarantined by the audit (excluded from reuse until verified):
 <table><tr><th>Member</th><th>Reason</th></tr>
 {% for q in ctx.audit_quarantined_list %}<tr><td>{{ q.id }}</td><td>{{ q.reason }}</td></tr>{% endfor %}
 </table></div>{% endif %}{% endif %}
{% if ctx.n_imperfection_governed %}<div class="warn">⚠ {{ ctx.n_imperfection_governed }} reused
 member(s) are governed by a load combination other than plain gravity (e.g. the EN 1993-1-1 §5.3.2
 sway-imperfection case) — the member is sized by the worst case across the combination envelope.</div>{% endif %}
{% if ctx.cut_donors %}<div class="warn">✂ Cutting-stock: {{ ctx.cut_donors }} donor(s) were cut to
 length (some into several pieces for several slots), leaving {{ ctx.reusable_remainder_m }} m of
 reusable remainder returned to stock.</div>{% endif %}
{% if ctx.ltb_restraint_reliant %}<div class="warn">⚠ {{ ctx.ltb_restraint_reliant }} reused beam(s)
 pass bending only because the floor slab restrains the compression flange (&chi;<sub>LT</sub> would
 fall below 0.85 if unrestrained) — confirm the restraint, especially at the construction stage before
 the slab is composite.</div>{% endif %}
{% if ctx.unknown %}<div class="warn">⚠ {{ ctx.unknown }} donor member(s) across
 {{ ctx.unknown_kinds }} type(s) unidentified and excluded (not in the steel catalog — e.g. concrete,
 bar joists, or shapes outside the W-shape set). Add steel ones to the catalog or an override CSV.
 <table><tr><th>Unidentified type</th><th>Count</th></tr>
 {% for b in ctx.unknown_breakdown %}<tr><td>{{ b.name }}</td><td>{{ b.count }}</td></tr>{% endfor %}
 </table></div>{% endif %}
{% if ctx.pareto %}<h2>Objective trade-off</h2>
<p class="note">The same feasible donor–slot pairs solved under each goal — what "best" means is a
policy choice, and this table shows what each choice costs in the other currencies. The row marked
★ is the objective this report's assignments follow.</p>
<table><tr><th></th><th>Objective</th><th>Members reused</th><th>CO2e saved (kg)</th>
<th>Reclaimed steel reused (kg)</th><th>Optimality</th></tr>
{% for p in ctx.pareto %}<tr>
 <td>{{ '★' if p.selected else '' }}</td><td>{{ p.label }}</td><td>{{ p.n_reused }}</td>
 <td>{{ p.co2_saved_kg }}</td><td>{{ p.mass_reused_kg }}</td>
 <td>{{ 'proven optimal' if p.proven_optimal else 'heuristic — not proven' }}</td></tr>{% endfor %}
</table>{% endif %}
{% if ctx.disposition_present %}<h2>Stock disposition (unused donors)</h2>
<p class="note">What should happen to the {{ ctx.disposition_totals.n }} unused donor(s):
<b>{{ ctx.disposition_totals.store }}</b> worth storing (still feasible for an unfilled slot here at a
positive saving), <b>{{ ctx.disposition_totals.reroll }}</b> advised for direct re-rolling
(pilot-scale route — research-grade credit), <b>{{ ctx.disposition_totals.recycle }}</b> for
conventional EAF recycling. Potential end-of-life credits if none were stored:
{{ ctx.disposition_totals.reroll_credit_kg }} kg CO2e via re-rolling (eligible stock) plus
{{ ctx.disposition_totals.recycle_credit_kg }} kg CO2e via recycling (all stock — the two are
alternatives per member, not additive). Summarized by section; per-donor rows via
<code>--disposition-csv</code>.</p>
<table><tr><th>Section</th><th>Donors</th><th>Store</th><th>Re-roll</th><th>Recycle</th>
<th>Re-roll credit (kg CO2e)</th><th>Recycle credit (kg CO2e)</th></tr>
{% for d in ctx.disposition_by_section %}<tr>
 <td>{{ d.section }}</td><td>{{ d.n }}</td><td>{{ d.store }}</td><td>{{ d.reroll }}</td>
 <td>{{ d.recycle }}</td><td>{{ d.reroll_credit_kg }}</td><td>{{ d.recycle_credit_kg }}</td></tr>{% endfor %}
</table>{% endif %}
<p>Mapped {{ ctx.mapped }} · fuzzy {{ ctx.fuzzy }} · unknown {{ ctx.unknown }} ·
 {{ ctx.distinct_sections }} distinct donor section(s) used{% if ctx.max_distinct_sections is not none %}
 (cap {{ ctx.max_distinct_sections }}){% endif %} ·
 {{ ctx.match_optimality }} (solver: {{ ctx.solver_status }})</p>
<p class="disc">{{ ctx.disclaimer }}</p>
</body></html>"""


def render_html(ctx: dict, narrative: str, narrative_source: str = "deterministic") -> str:
    try:
        from jinja2 import Template
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            'HTML report rendering needs Jinja2 — install it with: pip install "steelreuse[report]"'
        ) from e

    return Template(_TEMPLATE).render(ctx=ctx, narrative=narrative, narrative_source=narrative_source)
