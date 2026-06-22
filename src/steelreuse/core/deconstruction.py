"""Deconstruction recovery model: turn a member's surveyed connection type into a recovery treatment
(cutting allowance + reuse-process carbon multiplier) and a geometric connections-per-member degree.

Pure, standard library only. Honest-by-default: a member with no surveyed connection data (or
``unknown``) gets no penalty, exactly like the PDA condition/verification fields. The reuse question
is per-member ("can I extract THIS member intact?"), so ``connection_type`` is the member's hardest
end; no shared connection object is modelled.
"""

from __future__ import annotations

from dataclasses import dataclass

from .frame import snap_nodes

# Connection types that cannot be deconstructed intact -> the member must be cut at both ends.
_CUT_TYPES = {"welded", "riveted"}
# Surveyed deconstructability override -> treatment, bypassing the type-derived rule.
_OVERRIDE_CLEAN = {"easy"}
_OVERRIDE_CUT = {"hard"}


@dataclass(frozen=True)
class DeconstructionPolicy:
    """Parameters for the recovery model (overridable; defaults are deliberately conservative)."""

    cut_allowance_mm: float = 60.0             # material lost per cut end (saw kerf + clean-up)
    welded_process_multiplier: float = 1.4     # reuse-process carbon uplift for cut-and-clean ends
    riveted_process_multiplier: float = 1.5    # riveted: drill-out + heavier clean-up
    min_stock_mm: float = 1000.0               # recoverable length never floored below this


@dataclass(frozen=True)
class Treatment:
    cut_ends: int               # number of ends that must be cut (0 or 2)
    cut_total_mm: float         # total length lost to cutting
    process_multiplier: float   # reuse-process carbon multiplier (1.0 = clean)
    note: str = ""


def _must_cut(member) -> tuple[bool, str]:
    """Whether the member's ends require cutting, honouring an explicit deconstructability override."""
    decon = (getattr(member, "deconstructability", None) or "").strip().lower()
    if decon in _OVERRIDE_CLEAN:
        return False, "surveyed deconstructability: easy"
    if decon in _OVERRIDE_CUT:
        return True, "surveyed deconstructability: hard"
    ctype = (getattr(member, "connection_type", None) or "").strip().lower()
    if ctype in _CUT_TYPES:
        return True, "surveyed connection: " + ctype
    return False, ""


def deconstruction_treatment(member, policy: DeconstructionPolicy | None = None) -> Treatment:
    """Recovery treatment for one member from its surveyed connection data."""
    p = policy or DeconstructionPolicy()
    must_cut, note = _must_cut(member)
    if not must_cut:
        return Treatment(0, 0.0, 1.0, note)
    ctype = (getattr(member, "connection_type", None) or "").strip().lower()
    mult = p.riveted_process_multiplier if ctype == "riveted" else p.welded_process_multiplier
    return Treatment(2, 2.0 * p.cut_allowance_mm, mult, note)


def effective_recoverable_length(member, policy: DeconstructionPolicy | None = None) -> float:
    """Usable length after both the PDA recoverable length and the connection cutting allowance.

    Composes with the PDA: starts from ``recoverable_length_mm`` (or physical ``length_mm`` when
    unsurveyed), subtracts the cutting allowance, and never floors below ``min_stock_mm``.
    """
    p = policy or DeconstructionPolicy()
    base = getattr(member, "recoverable_length_mm", None)
    if base is None or base <= 0:
        base = float(getattr(member, "length_mm", 0.0) or 0.0)
    t = deconstruction_treatment(member, p)
    return max(p.min_stock_mm, base - t.cut_total_mm)
