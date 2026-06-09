"""Report builder: deterministic numbers + (optional) LLM narrative.

Hard rule (CLAUDE.md): numbers are computed in Python and injected by Jinja2. The LLM only writes
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


def build_report_context(res: PipelineResult) -> dict:
    """Flatten a :class:`PipelineResult` into a JSON-ish dict of pre-computed values for the template."""
    m = res.match
    p = res.passport
    assignments = [
        {
            "slot": a.slot_id, "supply": a.supply_id, "section": a.section,
            "utilization": a.utilization, "status": a.status,
            "offcut_mm": a.offcut_mm, "co2_saved_kg": a.co2_saved_kg,
            "chi_lt": a.chi_lt, "chi_lt_if_free": a.chi_lt_if_free,
            "governing": a.governing_combination,
        }
        for a in m.assignments
    ]
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
        "match_co2_saved_kg": round(m.total_co2_saved_kg, 1),
        "total_offcut_mm": round(m.total_offcut_mm, 1),
        "n_unmatched": len(m.unmatched_slots),
        "n_unused": len(m.unused_supply),
        "unmatched_slots": m.unmatched_slots,
        "unused_supply": m.unused_supply,
        "solver_status": m.solver_status,
        "assignments": assignments,
        "ltb_restraint_reliant": ltb_restraint_reliant,
        "n_imperfection_governed": n_imperfection_governed,
        "cut_donors": len(m.donor_leftover_mm),
        "reusable_remainder_m": round(m.total_donor_leftover_mm / 1000.0, 1),
        "disclaimer": SCOPE_DISCLAIMER,
    }
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
    """A safe, number-faithful summary written purely from the computed context."""
    parts = [
        f"Of {ctx['slot_count']} demand slot(s), {ctx['n_reused']} were matched to reclaimed "
        f"members, avoiding about {ctx['match_co2_saved_kg']} kg CO2e of new-steel production.",
    ]
    if ctx["unmatched_slots"]:
        parts.append(f"{ctx['n_unmatched']} slot(s) found no suitable reclaimed member "
                     "and would need new steel.")
    if ctx["unused_supply"]:
        parts.append(f"{ctx['n_unused']} reclaimed member(s) were left unused and remain "
                     "available for other projects.")
    if ctx["unknown"]:
        top = "; ".join(f"{b['count']}x {b['name']}" for b in ctx["unknown_breakdown"][:5])
        parts.append(f"{ctx['unknown']} donor member(s) across {ctx['unknown_kinds']} type(s) could "
                     f"not be identified and were excluded from analysis (top: {top}).")
    return " ".join(parts)


def generate_narrative(ctx: dict, provider: LLMProvider | None = None) -> tuple[str, str]:
    """Return (narrative, source). Falls back to the deterministic text if no/invalid LLM output."""
    provider = provider or NullProvider()
    if isinstance(provider, NullProvider):
        return deterministic_narrative(ctx), "deterministic"

    system = (
        "You are a structural reuse assistant. Write a concise, plain-language narrative for an "
        "engineer reviewing a steel-reuse matching report. CRITICAL: do not perform any arithmetic "
        "and do not introduce any numbers that are not already in the data provided. Only use the "
        "figures given. Keep it under 150 words."
    )
    prompt = f"Matching results (already computed, do not change any number):\n{ctx}"
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
</style></head><body>
<h1>Circular Structural Reuse — Matching Report</h1>
<p><em>{{ narrative }}</em> <span style="color:#888">(narrative: {{ narrative_source }})</span></p>
<div class="kpis">
 <div class="kpi"><b>{{ ctx.n_reused }}</b>members reused</div>
 <div class="kpi"><b>{{ ctx.match_co2_saved_kg }}</b>kg CO2e saved</div>
 <div class="kpi"><b>{{ ctx.donor_saved_co2_kg }}</b>kg CO2e in full donor stock</div>
 <div class="kpi"><b>{{ ctx.unmatched_slots|length }}</b>slots need new steel</div>
</div>
<h2>Assignments</h2>
<table><tr><th>Demand slot</th><th>Reclaimed member</th><th>Section</th><th>Utilization</th>
<th>Gov. load case</th><th>Status</th><th>&chi;<sub>LT</sub></th><th>Off-cut (mm)</th>
<th>CO2e saved (kg)</th></tr>
{% for a in ctx.assignments %}<tr>
 <td>{{ a.slot }}</td><td>{{ a.supply }}</td><td>{{ a.section }}</td>
 <td>{{ '%.2f'|format(a.utilization) }}</td>
 <td>{{ a.governing }}</td>
 <td class="{{ 'review' if a.status=='REVIEW' else '' }}">{{ a.status }}</td>
 <td>{% if a.chi_lt is none %}—{% else %}{{ '%.2f'|format(a.chi_lt) }}{% if a.chi_lt == 1.0 and a.chi_lt_if_free is not none and a.chi_lt_if_free < 0.85 %} <span class="review" title="would be {{ '%.2f'|format(a.chi_lt_if_free) }} if the flange were unrestrained">⚠</span>{% endif %}{% endif %}</td>
 <td>{{ a.offcut_mm }}</td><td>{{ a.co2_saved_kg }}</td></tr>{% endfor %}
</table>
{% if ctx.n_imperfection_governed %}<div class="warn">⚠ {{ ctx.n_imperfection_governed }} reused
 member(s) are governed by a load combination other than plain gravity (e.g. the EN 1993-1-1 §5.3.2
 sway-imperfection case) — the member is sized by the worst case across the combination envelope.</div>{% endif %}
{% if ctx.cut_donors %}<div class="warn">✂ Cutting-stock: {{ ctx.cut_donors }} donor(s) were each cut
 into several pieces to fill multiple slots, leaving {{ ctx.reusable_remainder_m }} m of reusable
 remainder returned to stock.</div>{% endif %}
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
<p>Mapped {{ ctx.mapped }} · fuzzy {{ ctx.fuzzy }} · unknown {{ ctx.unknown }} ·
 solver: {{ ctx.solver_status }}</p>
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
