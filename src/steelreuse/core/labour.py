"""EXPERIMENTAL — steel-only deconstruction labour sketch. NOT wired into the value case.

Parked, not deleted: estimating deconstruction labour from a structural model alone is misleading,
because the real cost is dominated by work this tool cannot see — soft-strip of architectural
finishes and services, scaffolding/access, asbestos removal, crane hire — all of which happen
regardless of the steel. :mod:`steelreuse.core.value_case` therefore reports the steel *prize*
(reclaimed value, reuse premium, CO2) and its *reliability*, and leaves the cost side to the
contractor's own estimate.

These helpers remain for exploration. They model the steel-handling portion only, in three tiers:
  1. per-MEMBER handling (rig, crane, lower, cut-to-transport) — genuinely per stick;
  2. per-JOINT disassembly — charged ONCE per shared node (a beam-column joint is one joint), split
     across the members meeting there by how hard each is to free (so a column several beams frame
     into carries only a small share — it comes apart as its beams do);
  3. a whole-building mobilisation, which the caller adds once (not per member).
The hours are parametric estimates (no single published source) but map to physical actions, so they
are inspectable and tunable per contractor — unlike one mass x rate blob.
"""

from __future__ import annotations

from dataclasses import dataclass

from .frame import snap_nodes

_END_DISCONNECT_HOURS = {"bolted": 0.3, "welded": 0.8, "riveted": 1.0}  # hours to free ONE end
_DEFAULT_END_HOURS = 0.3            # unsurveyed / unknown -> treated as bolted
_HANDLING_BASE_HOURS = 0.3         # hook-up + banksman, any member
_HANDLING_MASS_DIVISOR = 500.0     # +1 h per 500 kg craned / handled
_HANDLING_LENGTH_DIVISOR = 20000.0  # +1 h per 20 m (long members: extra care + cut-to-transport)


@dataclass(frozen=True)
class LabourEstimate:
    hours: float
    cost_gbp: float
    handling_hours: float   # per-member: rig, crane, lower, cut-to-transport
    joint_hours: float      # this member's share of the shared joint disassembly
    basis: str


def _end_disconnect_hours(member) -> float:
    ct = (getattr(member, "connection_type", None) or "").strip().lower()
    return _END_DISCONNECT_HOURS.get(ct, _DEFAULT_END_HOURS)


def joint_hours(donor) -> dict[str, float]:
    """Per-member share of joint-disassembly hours, from the snapped frame topology.

    Each shared node is one joint: its disassembly is charged ONCE (governed by the hardest
    connection meeting there) and split across the members at that node in proportion to how hard
    each is to free. Members without usable coordinates are absent (handling only).
    """
    try:
        topo = snap_nodes(donor.members)
    except Exception:
        return {}
    eff = {m.id: _end_disconnect_hours(m) for m in donor.members}
    node_members: dict[str, list[str]] = {}
    for mid, (i, j) in topo.member_nodes.items():
        node_members.setdefault(i, []).append(mid)
        node_members.setdefault(j, []).append(mid)
    out: dict[str, float] = dict.fromkeys(topo.member_nodes, 0.0)
    for mids in node_members.values():
        efforts = [eff.get(m, _DEFAULT_END_HOURS) for m in mids]
        joint = max(efforts)              # the hardest connection governs the joint disassembly
        total = sum(efforts) or 1.0
        for m, e in zip(mids, efforts, strict=False):
            out[m] += joint * (e / total)  # allocate the single joint cost by difficulty
    return out


def _handling_hours(mass_kg: float, length_mm: float) -> float:
    return (_HANDLING_BASE_HOURS + mass_kg / _HANDLING_MASS_DIVISOR
            + length_mm / _HANDLING_LENGTH_DIVISOR)


def labour_estimate(
    mass_kg: float,
    length_mm: float,
    member_joint_hours: float,
    labour_rate_per_hour: float = 55.0,
) -> LabourEstimate:
    """Marginal steel-handling labour to bring ONE member down, given the crew is already mobilised.

    = per-member handling (mass/length) + this member's share of the shared joint disassembly
    (``member_joint_hours``, from :func:`joint_hours`). Mobilisation is the caller's, added once.
    """
    handling = _handling_hours(mass_kg, length_mm)
    hours = round(handling + member_joint_hours, 2)
    cost = round(hours * labour_rate_per_hour, 2)
    basis = f"handling {handling:.1f}h + shared joints {member_joint_hours:.1f}h"
    return LabourEstimate(hours=hours, cost_gbp=cost,
                          handling_hours=round(handling, 2),
                          joint_hours=round(member_joint_hours, 2), basis=basis)
