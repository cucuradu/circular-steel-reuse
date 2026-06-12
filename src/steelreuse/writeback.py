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
