"""Connection feasibility screen — geometric compatibility of a reclaimed member with the slot.

Connections (bolts, welds, plates) often govern whether reuse is *practical*, yet full connection
design is out of this tool's scope. This module is the honest middle ground: a **geometric
compatibility screen** between the donor section and the section the new design specified for the
slot. The design section is what the slot's connections (fin plates, end plates, base plates,
splices) were detailed around, so a donor that deviates too far from it forces a connection redesign
even when the member checks pass.

The screen is deliberately *not* a capacity check — member strength is the EN 1993-1-1 checker's job —
and it never overrides it. It asks only: could this donor plausibly be connected into the position
detailed for the design section?

Rules (all geometric, all conservative defaults, all overridable via :class:`ConnectionPolicy`):
  * **Shape family** — an open (I/H) position cannot take a tube and vice versa: the connection
    typology itself changes -> ``incompatible``.
  * **Depth, over** — a donor much deeper than the design section does not fit the detailed
    structural zone / connection geometry -> ``incompatible`` beyond ``max_depth_over_mm``.
  * **Depth, under** — a much shallower donor leaves bolts/plates misplaced and is a different
    stiffness class -> ``review`` (the EN check already governs strength/deflection).
  * **Web thickness** — fin-plate / web-bolt bearing scales with ``t_w`` -> ``review`` when the
    donor web is much thinner than the design web.
  * **Flange width** — seats, end plates and flange splices need width -> ``review`` when the donor
    flange is much narrower.

``review`` never gates a match — it surfaces in the report exactly like a structural REVIEW.
``incompatible`` gates the pair only when the screen is enabled (``--connections``); otherwise it is
reported as an annotation. A slot with no known design section yields ``unknown`` (no opinion):
absence of data never blocks reuse, mirroring the pre-demolition-audit philosophy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .sections import SectionProps


@dataclass(frozen=True)
class ConnectionPolicy:
    """Tolerances for the geometric compatibility screen (mm / ratios of the design value)."""

    max_depth_over_mm: float = 50.0    # donor deeper than design by more than this -> incompatible
    max_depth_under_frac: float = 0.25  # donor shallower than (1-frac)*design depth -> review
    min_web_ratio: float = 0.80        # donor t_w below this fraction of design t_w -> review
    min_flange_ratio: float = 0.70     # donor b below this fraction of design b -> review


@dataclass
class ConnectionCheck:
    status: str            # "ok" | "review" | "incompatible" | "unknown"
    notes: list[str]

    @property
    def note(self) -> str:
        return "; ".join(self.notes)


def screen_pair(
    donor: SectionProps,
    design: SectionProps | None,
    policy: ConnectionPolicy | None = None,
) -> ConnectionCheck:
    """Geometric connection-compatibility of ``donor`` standing in for ``design``.

    ``design is None`` (the slot never specified / never mapped a section) returns ``unknown`` —
    there is nothing to compare against, and no opinion is honest opinion.
    """
    p = policy or ConnectionPolicy()
    if design is None:
        return ConnectionCheck("unknown", ["no design section to compare against"])

    if donor.is_hollow != design.is_hollow:
        kind = ("hollow donor in an open-section position" if donor.is_hollow
                else "open-section donor in a hollow position")
        return ConnectionCheck(
            "incompatible", [kind + ": connection typology must be redesigned"])

    notes: list[str] = []
    over = donor.h - design.h
    if over > p.max_depth_over_mm:
        return ConnectionCheck(
            "incompatible",
            [f"donor {over:.0f} mm deeper than the design section "
             f"(> {p.max_depth_over_mm:.0f} mm): does not fit the detailed zone"],
        )
    if donor.h < (1.0 - p.max_depth_under_frac) * design.h:
        notes.append(
            f"donor {design.h - donor.h:.0f} mm shallower than the design section: "
            "end connections to re-detail"
        )
    if donor.tw < p.min_web_ratio * design.tw:
        notes.append(
            f"donor web {donor.tw:.1f} mm vs design {design.tw:.1f} mm: re-verify bolt bearing"
        )
    if donor.b < p.min_flange_ratio * design.b:
        notes.append(
            f"donor flange {donor.b:.0f} mm vs design {design.b:.0f} mm: "
            "re-detail seats/end plates"
        )
    if notes:
        return ConnectionCheck("review", notes)
    return ConnectionCheck("ok", [])
