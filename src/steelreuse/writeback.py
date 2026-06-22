"""Reshape a :class:`~steelreuse.pipeline.PipelineResult` into a per-element status map for writing
back to the BIM model the members were extracted from (e.g. a pyRevit "Apply Matches" button that
colours elements by reuse outcome).

This is pure reshaping of values :func:`steelreuse.pipeline.run_pipeline` already computed — no new
arithmetic beyond summing per-assignment CO2 savings onto their element, per Hard rule 1 (the LLM
never does arithmetic; this module isn't an LLM, but the principle of "compute once, in Python,
then just relabel" still applies).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .pipeline import PipelineResult

# RGB colours (0-255) for each status, for a Revit OverrideGraphicSettings solid fill / line colour.
# ``None`` means "no override" (leave the element as the model defines it).
DONOR_COLORS: dict[str, tuple[int, int, int] | None] = {
    "reused": (0, 166, 81),        # green
    "available": (160, 160, 160),  # grey
    "quarantined": (214, 39, 40),  # red
    "unmapped": (90, 90, 90),       # dark grey
}

DEMAND_COLORS: dict[str, tuple[int, int, int] | None] = {
    "filled": (0, 166, 81),        # green
    "partially_filled": (255, 191, 0),  # amber
    "unfilled": (255, 127, 14),    # orange
    "non_steel": None,
}


@dataclass(frozen=True)
class ElementStatus:
    status: str
    color: tuple[int, int, int] | None
    note: str = ""
    # Structured pairing data, so the Revit button can fill schedulable parameters without
    # parsing the note text. Donor side: the slot id(s) this member fills; demand side: the
    # donor element id(s) that fill it.
    paired_with: str = ""
    co2_saved_kg: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _donor_status(result: PipelineResult) -> dict[str, ElementStatus]:
    if result.donor is None:
        return {}
    # One donor can fill several slots under cutting-stock (--cut), so group, don't overwrite.
    assignments: dict[str, list] = {}
    for a in result.match.assignments:
        assignments.setdefault(a.supply_id, []).append(a)
    quarantine_reasons = dict(result.audit.quarantined) if result.audit else {}
    used_supply_ids = set(assignments)
    available_ids = set(result.match.unused_supply)

    out: dict[str, ElementStatus] = {}
    for m in result.donor.members:
        if m.id in used_supply_ids:
            assigns = assignments[m.id]
            a = assigns[0]
            slot_ids = ", ".join(x.slot_id for x in assigns)
            co2 = sum(x.co2_saved_kg for x in assigns)
            if len(assigns) == 1:
                note = f"reused -> slot {a.slot_id} ({a.section}), saved {co2:.0f} kg CO2e"
            else:
                note = (f"reused (cut into {len(assigns)}) -> slots {slot_ids} ({a.section}), "
                        f"saved {co2:.0f} kg CO2e")
            out[m.id] = ElementStatus("reused", DONOR_COLORS["reused"], note,
                                      paired_with=slot_ids, co2_saved_kg=co2)
        elif m.id in quarantine_reasons:
            out[m.id] = ElementStatus(
                "quarantined", DONOR_COLORS["quarantined"], quarantine_reasons[m.id]
            )
        elif m.id in available_ids:
            out[m.id] = ElementStatus(
                "available", DONOR_COLORS["available"], f"mapped ({m.section}), not selected"
            )
        elif not m.section:
            out[m.id] = ElementStatus(
                "unmapped", DONOR_COLORS["unmapped"], f"section not recognized: {m.raw_section}"
            )
    return out


def _demand_status(result: PipelineResult) -> dict[str, ElementStatus]:
    if result.demand is None:
        return {}
    filled_slots = {a.slot_id: a for a in result.match.assignments}
    unmatched_slots = set(result.match.unmatched_slots)

    by_member: dict[str, list[tuple[bool, str]]] = {}
    for slot in result.slots:
        is_filled = slot.id in filled_slots
        if not is_filled and slot.id not in unmatched_slots:
            continue  # shouldn't happen, but don't claim a status we can't justify
        by_member.setdefault(slot.member_id, []).append((is_filled, slot.id))

    out: dict[str, ElementStatus] = {}
    for member_id, spans in by_member.items():
        filled = [s for ok, s in spans if ok]
        unfilled = [s for ok, s in spans if not ok]
        assigns = [filled_slots[s] for s in filled]
        donor_ids = ", ".join(sorted({a.supply_id for a in assigns}))
        co2 = sum(a.co2_saved_kg for a in assigns) if assigns else None
        if filled and not unfilled:
            a = assigns[0]
            note = f"filled by reuse: {donor_ids} ({a.section})"
            out[member_id] = ElementStatus("filled", DEMAND_COLORS["filled"], note,
                                           paired_with=donor_ids, co2_saved_kg=co2)
        elif filled and unfilled:
            note = f"{len(filled)}/{len(spans)} spans filled by reuse, {len(unfilled)} need new steel"
            out[member_id] = ElementStatus(
                "partially_filled", DEMAND_COLORS["partially_filled"], note,
                paired_with=donor_ids, co2_saved_kg=co2,
            )
        else:
            out[member_id] = ElementStatus(
                "unfilled", DEMAND_COLORS["unfilled"], "no matching donor found; new steel required"
            )

    for m in result.demand.members:
        if m.id not in out:
            out[m.id] = ElementStatus("non_steel", DEMAND_COLORS["non_steel"], "")
    return out


def build_writeback(result: PipelineResult) -> dict:
    """Return ``{"donor": {element_id: {status, color, note}}, "demand": {...}, "summary": {...}}``.

    Requires ``result.donor``/``result.demand``/``result.slots`` (set by
    :func:`steelreuse.pipeline.run_pipeline`). Element ids are the Revit ``ElementId`` strings
    captured by the extractor, so a pyRevit button can look elements up directly with
    ``doc.GetElement(ElementId(int(id)))``. The ``summary`` block carries the run's headline
    numbers (already computed by the pipeline — only relabelled here) so the button can print
    them inside Revit without re-deriving anything.
    """
    donor = {k: v.to_dict() for k, v in _donor_status(result).items()}
    demand = {k: v.to_dict() for k, v in _demand_status(result).items()}

    def _counts(side: dict) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in side.values():
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        return counts

    return {
        "donor": donor,
        "demand": demand,
        "summary": {
            "donor_counts": _counts(donor),
            "demand_counts": _counts(demand),
            "n_reused": result.match.n_reused,
            "slot_count": result.slot_count,
            "supply_count": result.supply_count,
            "co2_saved_kg": result.match.total_co2_saved_kg,
        },
    }


def status_from_results(results: dict) -> dict:
    """Reconstruct the apply-matches status map (``build_writeback`` shape) from a ``results.json``.

    For re-applying a *saved* run whose original ``status.json`` was not archived: the assignment-keyed
    results.json (schema v2, :func:`build_results`) carries enough to recover the elements that get a
    colour — reused / quarantined donors and filled / partially-filled / unfilled demand. The purely
    informational states absent from results.json (donor ``available``/``unmapped``, demand
    ``non_steel`` — grey or no-override) are simply omitted, so those elements are left as-is. Colours
    and statuses mirror :data:`DONOR_COLORS` / :data:`DEMAND_COLORS` exactly.
    """
    assignments = results.get("assignments") or []
    unfilled = results.get("unfilled") or []

    def _rgb(color):
        return list(color) if color is not None else None

    # Donor side: group assignments by donor, mark reused; add quarantined donors.
    donor: dict[str, dict] = {}
    by_donor: dict[str, list] = {}
    for a in assignments:
        by_donor.setdefault(a.get("donor_id", ""), []).append(a)
    by_donor.pop("", None)
    for donor_id, assigns in by_donor.items():
        slot_ids = ", ".join(a.get("slot_id", "") for a in assigns)
        co2 = sum(a.get("co2_saved_kg") or 0.0 for a in assigns)
        section = assigns[0].get("donor_section", "")
        if len(assigns) == 1:
            note = f"reused -> slot {assigns[0].get('slot_id', '')} ({section}), saved {co2:.0f} kg CO2e"
        else:
            note = (f"reused (cut into {len(assigns)}) -> slots {slot_ids} ({section}), "
                    f"saved {co2:.0f} kg CO2e")
        donor[donor_id] = ElementStatus("reused", DONOR_COLORS["reused"], note,
                                        paired_with=slot_ids, co2_saved_kg=co2).to_dict()
    for q in results.get("quarantined_donors") or []:
        donor[q.get("donor_id", "")] = ElementStatus(
            "quarantined", DONOR_COLORS["quarantined"], q.get("reason", "")).to_dict()
    donor.pop("", None)

    # Demand side: per member, which spans were filled vs left for new steel.
    filled_by_member: dict[str, list] = {}
    for a in assignments:
        filled_by_member.setdefault(a.get("demand_id", ""), []).append(a)
    unfilled_by_member: dict[str, list] = {}
    for u in unfilled:
        unfilled_by_member.setdefault(u.get("demand_id", ""), []).append(u.get("slot_id", ""))

    demand: dict[str, dict] = {}
    for member_id in set(filled_by_member) | set(unfilled_by_member):
        if not member_id:
            continue
        fa = filled_by_member.get(member_id, [])
        uf = unfilled_by_member.get(member_id, [])
        donor_ids = ", ".join(sorted({a.get("donor_id", "") for a in fa}))
        co2 = sum(a.get("co2_saved_kg") or 0.0 for a in fa) if fa else None
        n_spans = len(fa) + len(uf)
        if fa and not uf:
            note = f"filled by reuse: {donor_ids} ({fa[0].get('donor_section', '')})"
            demand[member_id] = ElementStatus("filled", DEMAND_COLORS["filled"], note,
                                              paired_with=donor_ids, co2_saved_kg=co2).to_dict()
        elif fa and uf:
            note = f"{len(fa)}/{n_spans} spans filled by reuse, {len(uf)} need new steel"
            demand[member_id] = ElementStatus(
                "partially_filled", DEMAND_COLORS["partially_filled"], note,
                paired_with=donor_ids, co2_saved_kg=co2).to_dict()
        else:
            demand[member_id] = ElementStatus(
                "unfilled", DEMAND_COLORS["unfilled"],
                "no matching donor found; new steel required").to_dict()

    for v in donor.values():
        v["color"] = _rgb(v["color"])
    for v in demand.values():
        v["color"] = _rgb(v["color"])

    kpis = results.get("kpis") or {}
    return {
        "donor": donor,
        "demand": demand,
        "summary": {
            "n_reused": kpis.get("reused"),
            "slot_count": kpis.get("slots"),
            "supply_count": kpis.get("supply_count"),
            "co2_saved_kg": kpis.get("co2_saved_kg"),
        },
    }


def _mass_reused_kg(result: PipelineResult) -> float:
    """Reclaimed steel mass put back to work = each reused donor's catalog mass over its used length.

    Mirrors the canonical formula in :func:`steelreuse.pipeline` (``catalog[section].mass_kgm *
    slot.required_length_mm``) -- the same number the Pareto/CLI report, just summed here. No new
    structural arithmetic (docs/DESIGN_PRINCIPLES.md hard rule 1).
    """
    from .core.sections import load_default_catalog
    catalog = load_default_catalog()
    slot_by_id = {s.id: s for s in (result.slots or [])}
    total = 0.0
    for a in result.match.assignments:
        sec = catalog.get(a.section)
        slot = slot_by_id.get(a.slot_id)
        if sec is not None and slot is not None:
            total += sec.mass_kgm * slot.required_length_mm / 1000.0
    return total


def build_results(result: PipelineResult) -> dict:
    """Return the assignment-keyed ``results.json`` contract (schema v2) the Revit panel consumes.

    A sibling of :func:`build_writeback`: that one is *element*-keyed (drives the Apply-Matches
    colouring); this one is *assignment*-keyed (drives the filterable results panel). Schema v2
    serialises the full report context -- KPIs, diagnosis, warnings, and the optional portfolio /
    pareto / disposition / audit blocks -- so the panel can render every result view. Pure reshaping
    of values :func:`steelreuse.pipeline.run_pipeline` + :func:`build_report_context` already
    computed (docs/DESIGN_PRINCIPLES.md hard rule 1 -- no new arithmetic). Readers branch on
    ``schema_version``.
    """
    from .llm.report import build_report_context  # lazy: keeps writeback import-light, no cycle
    ctx = build_report_context(result)
    m = result.match
    slots_by_id = {s.id: s for s in (result.slots or [])}
    donor_by_id = {d.id: d for d in result.donor.members} if result.donor else {}

    assignments = []
    for a in ctx["assignments"]:
        slot = slots_by_id.get(a["slot"])
        assignments.append({
            "demand_id": slot.member_id if slot else "",
            "slot_id": a["slot"],
            "demand_section": (slot.design_section if slot else None) or "",
            "donor_id": a["supply"],
            "donor_section": a["section"],
            "utilization": a["utilization"],
            "governing_combo": a["governing"],
            "check_status": a["status"],
            "chi_lt": a["chi_lt"],
            "chi_lt_if_free": a["chi_lt_if_free"],
            "offcut_mm": a["offcut_mm"],
            "co2_saved_kg": a["co2_saved_kg"],
            "connection": a["connection"],
            "connection_review": a["connection"] == "review",
            "verification": a["verification"],
            "condition": a["condition"],
            "knockdown": a["knockdown"],
        })

    unfilled = []
    for slot_id in m.unmatched_slots:
        slot = slots_by_id.get(slot_id)
        if slot is None:
            continue
        unfilled.append({
            "demand_id": slot.member_id,
            "slot_id": slot.id,
            "demand_section": slot.design_section or "",
        })

    quarantined_donors = []
    quarantine_reasons = dict(result.audit.quarantined) if result.audit else {}
    for donor_id, reason in quarantine_reasons.items():
        member = donor_by_id.get(donor_id)
        quarantined_donors.append({
            "donor_id": donor_id,
            "donor_section": (member.section if member else "") or "",
            "reason": reason,
        })

    out = {
        "schema_version": 2,
        "kpis": {
            "slots": ctx["slot_count"],
            "reused": ctx["n_reused"],
            "co2_saved_kg": ctx["match_co2_saved_kg"],
            "objective": m.objective,
            "proven_optimal": m.proven_optimal,
            "supply_count": ctx["supply_count"],
            "mass_reused_kg": round(_mass_reused_kg(result), 1),
            "distinct_sections": ctx["distinct_sections"],
            "max_distinct_sections": ctx["max_distinct_sections"],
            "reuse_rate_pct": ctx["reuse_rate_pct"],
            "match_optimality": ctx["match_optimality"],
            "solver_status": ctx["solver_status"],
            "donor_saved_co2_kg": ctx["donor_saved_co2_kg"],
        },
        "diagnosis": ctx.get("diagnosis") or {},
        "assignments": assignments,
        "unfilled": unfilled,
        "quarantined_donors": quarantined_donors,
        "warnings": {
            "ltb_restraint_reliant": ctx["ltb_restraint_reliant"],
            "imperfection_governed": ctx["n_imperfection_governed"],
            "cut_donors": ctx["cut_donors"],
            "reusable_remainder_m": ctx["reusable_remainder_m"],
            "unknown": ctx["unknown"],
            "unknown_breakdown": ctx["unknown_breakdown"],
            "connection_review": ctx["connection_review"],
        },
        "paths": {},  # stamped by the CLI/runner that knows the output folder; panel tolerates absence
    }
    if "projects" in ctx:
        out["portfolio"] = ctx["projects"]
    if "pareto" in ctx:
        out["pareto"] = ctx["pareto"]
    if ctx.get("disposition_present"):
        out["disposition"] = {"totals": ctx["disposition_totals"],
                              "by_section": ctx["disposition_by_section"]}
    if ctx.get("audit_present"):
        out["audit"] = {
            "audited": ctx["audit_audited"], "admitted": ctx["audit_admitted"],
            "quarantined": ctx["audit_quarantined"], "avg_knockdown": ctx["audit_avg_knockdown"],
            "verification": ctx["audit_verification"], "condition": ctx["audit_condition"],
            "quarantined_list": ctx["audit_quarantined_list"],
        }
    return out
