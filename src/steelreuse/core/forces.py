"""Pluggable force backend — turns demand members into design action effects (N_Ed, M_Ed, V_Ed).

Revit stores no internal forces, so we compute them here. Two backends share one interface:
  * :class:`AnalyticBackend` (default) — closed-form simply-supported results, no dependencies,
    CI-friendly. ``M = w L^2 / 8``, ``V = w L / 2``.
  * :class:`PyNiteBackend` (optional) — builds a 1-span frame in PyNiteFEA and solves it. For a
    simply-supported span it must agree with the analytic backend (that equivalence is a test).

SAP2000 backends (OAPI / table-scrape) plug in behind the same :class:`ForceBackend` protocol later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .ec3_checks import MemberDemand, c1_moment_gradient


class ForceBackend(Protocol):
    """Return (M_Ed [N*mm], V_Ed [N]) for a simply-supported beam span under a UDL."""

    def beam_span_forces(self, span_mm: float, udl_Npmm: float) -> tuple[float, float]: ...


class AnalyticBackend:
    """Closed-form simply-supported span. The always-available default."""

    name = "analytic"

    def beam_span_forces(self, span_mm: float, udl_Npmm: float) -> tuple[float, float]:
        M = udl_Npmm * span_mm**2 / 8.0
        V = udl_Npmm * span_mm / 2.0
        return M, V


class PyNiteBackend:
    """PyNiteFEA-based span solve (optional dependency, imported lazily)."""

    name = "pynite"

    def beam_span_forces(self, span_mm: float, udl_Npmm: float) -> tuple[float, float]:
        try:
            from Pynite import FEModel3D
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError("PyNiteFEA not installed; `uv pip install -e '.[fea]'`") from exc

        m = FEModel3D()
        m.add_material("steel", 210_000.0, 80_000.0, 0.3, 7.85e-9)
        # Generic stiff section: span forces of a determinate beam don't depend on section props.
        m.add_section("s", 1.0e4, 1.0e8, 1.0e8, 1.0e6)
        m.add_node("A", 0.0, 0.0, 0.0)
        m.add_node("B", span_mm, 0.0, 0.0)
        m.def_support("A", True, True, True, True, False, False)   # pin
        m.def_support("B", False, True, True, False, False, False)  # roller (free along X)
        m.add_member("bm", "A", "B", "steel", "s")
        # downward global-Y UDL (w1=w2); PyNite distributed loads use member local axes ~ global here
        m.add_member_dist_load("bm", "Fy", -udl_Npmm, -udl_Npmm)
        m.analyze_linear()
        bm = m.members["bm"]
        M = max(abs(bm.max_moment("Mz")), abs(bm.min_moment("Mz")))
        V = max(abs(bm.max_shear("Fy")), abs(bm.min_shear("Fy")))
        return M, V


# ---------------------------------------------------------------------------
# Load model -> MemberDemand
# ---------------------------------------------------------------------------

@dataclass
class Load:
    """Simple per-member load model used to derive demands when no analysis model is available."""

    udl_Npmm: float = 0.0          # beam uniformly distributed load (N/mm)
    axial_N: float = 0.0           # column axial (N), compression-positive
    w_service_Npmm: float | None = None  # service UDL for deflection (defaults to udl)
    axial_moment_Nmm: float = 0.0  # nominal column moment (e.g. from a notional eccentricity)


def member_demands(
    member,
    load: Load,
    backend: ForceBackend | None = None,
    ky: float = 1.0,
    kz: float = 1.0,
    compression_flange_restrained: bool = False,
    moment_shape: bool = False,
) -> list[MemberDemand]:
    """Build a :class:`MemberDemand` per structural span of a member.

    Beams: one demand per span in ``member.spans_mm`` (forces from the backend).
    Columns: a single axial demand over the full length (buckling length = member length).
    """
    backend = backend or AnalyticBackend()
    out: list[MemberDemand] = []

    if member.role == "column":
        # A nonzero moment (from a notional eccentricity) engages the N+M interaction check; left
        # unrestrained so LTB is considered conservatively when it does (docs/DESIGN_PRINCIPLES.md rule 4).
        out.append(MemberDemand(
            N_Ed=load.axial_N, My_Ed=load.axial_moment_Nmm,
            L=member.length_mm or 0.0, ky=ky, kz=kz,
        ))
        return out

    spans = member.spans_mm or ([member.length_mm] if member.length_mm else [])
    w_serv = load.w_service_Npmm if load.w_service_Npmm is not None else load.udl_Npmm
    # Moment-shape LTB factor: a simply-supported span under uniform load has the parabolic diagram
    # M(¼)=M(¾)=0.75·M_mid, so the 4-moment formula gives C1≈1.136 — sharper than the conservative
    # 1.0 (uniform-moment) default. Opt-in; off ⇒ C1 stays 1.0 and results are byte-identical.
    c1 = c1_moment_gradient(1.0, 0.75, 1.0, 0.75) if moment_shape else 1.0
    for span in spans:
        M, V = backend.beam_span_forces(span, load.udl_Npmm)
        out.append(MemberDemand(
            My_Ed=M, Vz_Ed=V, L=span, ky=ky, kz=kz,
            compression_flange_restrained=compression_flange_restrained,
            C1=c1, w_service=w_serv or None,
        ))
    return out


def required_bending_resistance(member, load: Load, backend: ForceBackend | None = None) -> float:
    """Peak M_Ed (N*mm) across a member's spans — handy for sizing/matching screens."""
    demands = member_demands(member, load, backend)
    return max((abs(d.My_Ed) for d in demands), default=0.0)
