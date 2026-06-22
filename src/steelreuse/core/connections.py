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
    """Tolerances for the geometric compatibility screen (mm / ratios of the design value),
    plus the parameters of the *standard simple end connection* used for the shear-capacity screen
    (:func:`standard_shear_capacity`): a single vertical row of bolts through a fin plate, the
    workhorse beam-to-column shear connection."""

    max_depth_over_mm: float = 50.0    # donor deeper than design by more than this -> incompatible
    max_depth_under_frac: float = 0.25  # donor shallower than (1-frac)*design depth -> review
    min_web_ratio: float = 0.80        # donor t_w below this fraction of design t_w -> review
    min_flange_ratio: float = 0.70     # donor b below this fraction of design b -> review
    # Standard fin-plate parameters (EN 1993-1-8): M20 class 8.8 bolts in an S275 plate.
    bolt_d_mm: float = 20.0            # bolt diameter
    bolt_area_mm2: float = 245.0       # tensile stress area A_s (M20)
    bolt_fub: float = 800.0            # bolt ultimate strength (class 8.8); alpha_v = 0.6 applies
    plate_t_mm: float = 10.0           # fin-plate thickness
    plate_fu: float = 430.0            # ultimate strength used for bearing (S275 plate; also used
                                       # for the beam web -> conservative for S355+ donors)
    end_dist_mm: float = 40.0          # e1, top/bottom edge distance in the web
    pitch_mm: float = 70.0             # p1, vertical bolt pitch
    max_bolt_rows: int = 8             # detailing cap
    gamma_m2: float = 1.25


@dataclass
class ConnectionCheck:
    status: str            # "ok" | "review" | "incompatible" | "unknown"
    notes: list[str]

    @property
    def note(self) -> str:
        return "; ".join(self.notes)


def standard_shear_capacity(
    sec: SectionProps,
    policy: ConnectionPolicy | None = None,
) -> tuple[float, int] | None:
    """Shear resistance (N) of a *standard* fin-plate connection on this beam, and its bolt-row count.

    A capacity screen, not a design: one vertical row of ``n`` bolts (as many as the clear web depth
    accommodates at pitch ``p1`` with end distances ``e1``, capped at ``max_bolt_rows``), each worth
    the minimum of (EN 1993-1-8 Table 3.4):
      * bolt shear      ``F_v,Rd = 0.6 f_ub A_s / gamma_M2``  (alpha_v = 0.6, class 4.6/5.6/8.8);
      * bearing         ``F_b,Rd = 2.5 * 0.5 * f_u * d * t / gamma_M2`` on the thinner of beam web
        and fin plate, with the deliberately conservative ``alpha_b = 0.5`` (end-row value) so no
        spacing optimisation is assumed.
    Block tearing and plate shear are not evaluated — with these proportions (10 mm plate, alpha_b
    = 0.5) they do not govern a *standard* detail, and the screen's job is a credible lower bound.
    Returns ``None`` for hollow sections (a tube has no web to fin-plate into; different typology).
    """
    p = policy or ConnectionPolicy()
    if sec.is_hollow:
        return None
    clear_web = sec.h - 2.0 * sec.tf - 2.0 * sec.r
    rows = int((clear_web - 2.0 * p.end_dist_mm) // p.pitch_mm) + 1
    rows = max(1, min(p.max_bolt_rows, rows))
    f_v = 0.6 * p.bolt_fub * p.bolt_area_mm2 / p.gamma_m2
    f_b_web = 2.5 * 0.5 * p.plate_fu * p.bolt_d_mm * sec.tw / p.gamma_m2
    f_b_plate = 2.5 * 0.5 * p.plate_fu * p.bolt_d_mm * p.plate_t_mm / p.gamma_m2
    return rows * min(f_v, f_b_web, f_b_plate), rows


def screen_pair(
    donor: SectionProps,
    design: SectionProps | None,
    policy: ConnectionPolicy | None = None,
    v_ed_n: float = 0.0,
    donor_connection_type: str | None = None,
    donor_connection_condition: str | None = None,
) -> ConnectionCheck:
    """Geometric connection-compatibility of ``donor`` standing in for ``design``.

    ``design is None`` (the slot never specified / never mapped a section) returns ``unknown`` —
    there is nothing to compare against, and no opinion is honest opinion.

    ``v_ed_n`` (optional, N): the slot's worst shear demand. When given, the donor's *standard*
    fin-plate capacity (:func:`standard_shear_capacity`) is screened against it; exceeding it flags
    ``review`` ("a standard end connection won't carry this — bespoke design needed"), never
    ``incompatible`` (a bespoke connection may well work; that is the engineer's call).
    """
    p = policy or ConnectionPolicy()
    survey_notes: list[str] = []
    ctype = (donor_connection_type or "").strip().lower()
    if ctype in ("welded", "riveted"):
        survey_notes.append(
            "surveyed joint: " + ctype + " — verify the member can be recovered intact (cutting needed)")
    ccond = (donor_connection_condition or "").strip().upper()
    if ccond in ("C", "D"):
        survey_notes.append("surveyed joint condition " + ccond + ": inspect the connection")
    cap_notes: list[str] = list(survey_notes)
    if v_ed_n > 0:
        cap = standard_shear_capacity(donor, p)
        if cap is not None and v_ed_n > cap[0]:
            cap_notes.append(
                f"shear {v_ed_n / 1e3:.0f} kN exceeds a standard {cap[1]}-row fin plate "
                f"(~{cap[0] / 1e3:.0f} kN): bespoke end connection required"
            )
    if design is None:
        if cap_notes:
            return ConnectionCheck("review", cap_notes)
        return ConnectionCheck("unknown", ["no design section to compare against"])

    if donor.is_hollow != design.is_hollow:
        kind = ("hollow donor in an open-section position" if donor.is_hollow
                else "open-section donor in a hollow position")
        return ConnectionCheck(
            "incompatible", [kind + ": connection typology must be redesigned"])

    notes: list[str] = list(cap_notes)   # capacity flag (if any) joins the geometric findings
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
