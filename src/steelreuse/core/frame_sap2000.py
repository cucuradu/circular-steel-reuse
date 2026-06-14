"""Experimental SAP2000 (OAPI) frame backend — an optional, OFF-by-default alternative force source.

This is a drop-in for :func:`steelreuse.core.frame.analyze_frame`: same signature, same
:class:`~steelreuse.core.frame.FrameResult` return type. It reuses ``frame``'s **pure-Python
topology** (node snapping, column/span splitting, free-end pruning) and its **force-extraction
helpers** (``_governing_axial`` etc.) verbatim, swapping only the solver — so a force difference
between this and the PyNite path is solver numerics, not modelling choices. That equivalence is the
point of the cross-software benchmark (FUTURE_IMPROVEMENTS I-9, thesis §11).

Scope (deliberately small, see the 2026-06-13 design spec): the **default ULS gravity combination**
on *connectable* frames only. Lateral / 2nd-order cases (sway EHF, wind, seismic, P-Δ, α_cr) are out
of scope; a request for any of them is **refused** (``ok=False`` with a warning) rather than silently
ignored. SAP2000 is reached lazily through :mod:`steelreuse.core._sap2000`; when it is unavailable the
caller falls back to the analytic path exactly as with a missing PyNite.
"""

from __future__ import annotations

import math

from ._sap2000 import Sap2000Unavailable, sap2000_session
from .ec3_checks import MemberDemand
from .frame import (
    FrameOptions,
    FrameResult,
    _build_slots_by_member,
    _envelope_moment,
    _expand_spans_tracked,
    _governing_axial,
    _governing_shear,
    _section_args,
    _stabilize_topology,
    snap_nodes,
    split_columns_at_framing,
)

# SAP2000 enum constants used below (kept local so nothing imports comtypes here).
_E_STEEL = 210_000.0          # N/mm^2
_NU_STEEL = 0.3
_MAT_STEEL = 1                # eMatType_Steel
_DIR_GLOBAL_Z = 6            # SetLoadDistributed Dir: global Z (downward load = negative value)
_TYPE_FORCE_PER_LEN = 1      # SetLoadDistributed MyType: force/length
_LP_DEAD, _LP_LIVE = 1, 3    # eLoadPatternType: Dead, Live
_COMBO_LINEAR_ADD = 0        # RespCombo type: linear additive
_CNAME_LOADCASE = 0          # eCNameType: LoadCase
_ITEM_OBJECT_ELM = 0         # eItemTypeElm: ObjectElm (results keyed by the frame object)

# SAP2000 FrameForce component -> the PyNite-member axis name frame.py's extraction helpers ask for.
# frame.py local My = section major axis, Mz = minor; Fy/Fz = the two transverse shears. SAP2000
# reports M3 about the major (local-3) axis and M2 about the minor; V2/V3 are the matching shears.
_MOMENT_OF_AXIS = {"My": "M3", "Mz": "M2"}
_SHEAR_OF_DIR = {"Fy": "V2", "Fz": "V3"}


class _SapMemberForces:
    """Adapter giving SAP2000 station results the same method surface as a PyNite member, so
    frame.py's ``_governing_axial`` / ``_envelope_moment`` / ``_governing_shear`` work unchanged.

    Constructed with ``{combo: {"P","M3","M2","V2","V3": [station values...]}}`` in **SAP2000
    convention** (axial ``P`` tension-positive). The only sign-critical translation is axial:
    EN/PyNite is **compression-positive**, so the exposed axial is ``-P``. Moments and shears are
    consumed as magnitudes by the helpers, so component identity (major vs minor) is what matters,
    not their sign.
    """

    def __init__(self, by_combo: dict[str, dict[str, list[float]]]):
        self._by_combo = by_combo

    # -- axial: compression-positive = -P -------------------------------------------------
    def max_axial(self, combo: str) -> float:
        return -min(self._by_combo[combo]["P"])

    def min_axial(self, combo: str) -> float:
        return -max(self._by_combo[combo]["P"])

    # -- moments: My <- M3 (major), Mz <- M2 (minor) --------------------------------------
    def max_moment(self, axis: str, combo: str) -> float:
        return max(self._by_combo[combo][_MOMENT_OF_AXIS[axis]])

    def min_moment(self, axis: str, combo: str) -> float:
        return min(self._by_combo[combo][_MOMENT_OF_AXIS[axis]])

    # -- shears: Fy <- V2, Fz <- V3 -------------------------------------------------------
    def max_shear(self, direction: str, combo: str) -> float:
        return max(self._by_combo[combo][_SHEAR_OF_DIR[direction]])

    def min_shear(self, direction: str, combo: str) -> float:
        return min(self._by_combo[combo][_SHEAR_OF_DIR[direction]])


def _unsupported_cases(options: FrameOptions) -> list[str]:
    """Names of any requested load cases this gravity-only backend does not model."""
    out: list[str] = []
    if options.notional_phi > 0.0:
        out.append("sway imperfection (EHF)")
    if options.wind_kpa > 0.0:
        out.append("wind")
    if options.seismic_cs > 0.0:
        out.append("seismic")
    if options.second_order:
        out.append("2nd-order (P-Delta)")
    return out


def _fallback(demand_members, message: str) -> FrameResult:
    """Empty result that routes every member back to the analytic path (mirrors analyze_frame)."""
    return FrameResult(
        demands_by_member={},
        node_count=0,
        member_count=0,
        base_node_ids=[],
        skipped_member_ids=[m.id for m in demand_members],
        warnings=[message],
        ok=False,
    )


def analyze_frame_sap2000(
    demand_members,
    loads,
    catalog=None,
    combos=None,
    options: FrameOptions | None = None,
) -> FrameResult:
    """Solve the demand frame in SAP2000 (gravity only) and return a per-member force envelope.

    Signature mirrors :func:`steelreuse.core.frame.analyze_frame`. Out-of-scope load cases are
    refused up front (before any COM connection); see the module docstring.
    """
    options = options or FrameOptions()

    catalog = catalog or {}

    unsupported = _unsupported_cases(options)
    if unsupported:
        return _fallback(
            demand_members,
            f"SAP2000 backend is gravity-only — {', '.join(unsupported)} not supported "
            "(use the PyNite solver for these); falling back to analytic loads",
        )

    # --- topology: identical pure-Python prep to analyze_frame, so the two solvers see the SAME
    # node/member graph (the whole point of the cross-software benchmark) -----------------------
    columns_split = split_columns_at_framing(demand_members, options.snap_tol_mm)
    expanded, interior_ends = _expand_spans_tracked(columns_split)
    members_by_id = {m.id: m for m in expanded}
    topo = snap_nodes(expanded, options.snap_tol_mm, options.base_tol_mm)
    if options.prune_free_ends:
        _stabilize_topology(topo)
    column_nodes = {n for mid, ends in topo.member_nodes.items()
                    if members_by_id[mid].role == "column" for n in ends}
    if not topo.member_nodes or not topo.base_node_ids:
        return _fallback(demand_members, "no connectable geometry — using analytic loads")

    try:
        with sap2000_session() as model:
            return _solve_gravity(
                model, demand_members, members_by_id, topo, interior_ends, column_nodes,
                loads, catalog, combos, options)
    except Sap2000Unavailable as exc:
        return _fallback(demand_members, f"SAP2000 unavailable ({exc}) — using analytic loads")


def _solve_gravity(model, demand_members, members_by_id, topo, interior_ends, column_nodes,
                   loads, catalog, combos, options) -> FrameResult:
    """Build the gravity frame in SAP2000, solve, and extract a per-member force envelope.

    Mirrors the PyNite build in ``analyze_frame``: nodes → joints, fixed column bases, frame objects
    on a generic stiff section, major-axis (M3) end releases pinning beams at real supports, a
    dead/live UDL on the beams, and the single ULS gravity combination. Only this combo is requested
    from SAP (the SLS deflection check uses the analytic service UDL, as in the PyNite path).

    NB: every ``model.*`` call below executes only when SAP2000 is actually present; the call shapes
    follow the CSI OAPI and are validated by ``tests/test_sap2000_parity.py`` on the trial machine.
    """
    gamma_g, gamma_q = loads.gamma_g, loads.gamma_q
    combo_name = (combos[0][0] if combos else "ULS gravity")

    # Material + one generic stiff section (determinate span forces are section-independent, matching
    # the PyNite backend's single generic section).
    model.PropMaterial.SetMaterial("STEEL", _MAT_STEEL)
    model.PropMaterial.SetMPIsotropic("STEEL", _E_STEEL, _NU_STEEL, 1.2e-5)

    # Joints (UserName = our node id) and fixed/pinned base restraints.
    for n in topo.nodes.values():
        model.PointObj.AddCartesian(n.x, n.y, n.z, " ", n.name)
    base_restraint = [True] * 6 if options.fixed_base else [True, True, True, True, False, False]
    for nid in topo.base_node_ids:
        model.PointObj.SetRestraint(nid, base_restraint)

    # Frame objects + per-member generic section; pin beam ends (release major-axis M3) at real
    # supports exactly as analyze_frame does (interior beam-to-beam crossings stay continuous).
    beam_ids: list[str] = []
    for mid, (i, j) in topo.member_nodes.items():
        m = members_by_id[mid]
        sec = catalog.get(m.section) if m.section else None
        a, iy, iz, jt = _section_args(sec)
        sname = f"S_{mid}"
        # SetGeneral(name, mat, t3, t2, Area, As2, As3, Torsion, I33, I22, S33, S22, Z33, Z22, R33, R22)
        model.PropFrame.SetGeneral(sname, "STEEL", 1.0, 1.0, a, a, a, jt, iy, iz,
                                   1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        model.FrameObj.AddByPoint(i, j, " ", sname, mid)
        if options.pin_beams and m.role in ("beam", "brace"):
            rel_i = (mid, "i") not in interior_ends or i in column_nodes
            rel_j = (mid, "j") not in interior_ends or j in column_nodes
            # release vector order [P, V2, V3, T, M2, M3]; release M3 (major bending) at real supports
            ii = [False, False, False, False, False, rel_i]
            jj = [False, False, False, False, False, rel_j]
            if rel_i or rel_j:
                model.FrameObj.SetReleases(mid, ii, jj, [0.0] * 6, [0.0] * 6)
        if m.role == "beam":
            beam_ids.append(mid)

    # Load patterns + the beam UDLs (characteristic dead/live; columns load via the solved path).
    model.LoadPatterns.Add("DL", _LP_DEAD, 0.0, True)
    model.LoadPatterns.Add("LL", _LP_LIVE, 0.0, True)
    for mid in beam_ids:
        width = _beam_width(loads, mid)
        w_dead = loads.dead_kpa * width      # kN/m == N/mm
        w_live = loads.live_kpa * width
        model.FrameObj.SetLoadDistributed(mid, "DL", _TYPE_FORCE_PER_LEN, _DIR_GLOBAL_Z,
                                          0.0, 1.0, -w_dead, -w_dead)
        model.FrameObj.SetLoadDistributed(mid, "LL", _TYPE_FORCE_PER_LEN, _DIR_GLOBAL_Z,
                                          0.0, 1.0, -w_live, -w_live)

    # ULS gravity combination γ_G·DL + γ_Q·LL, and solve.
    model.RespCombo.Add(combo_name, _COMBO_LINEAR_ADD)
    model.RespCombo.SetCaseList(combo_name, _CNAME_LOADCASE, "DL", gamma_g)
    model.RespCombo.SetCaseList(combo_name, _CNAME_LOADCASE, "LL", gamma_q)
    # SAP2000 requires the model to be SAVED to a .sdb before it will run the analysis. Use a fresh,
    # NON-EXISTENT path: saving onto an existing file pops an invisible "overwrite?" dialog that
    # deadlocks a hidden instance.
    import os  # noqa: PLC0415 - only needed on the (optional) SAP2000 path
    import tempfile  # noqa: PLC0415
    import uuid  # noqa: PLC0415
    save_path = os.path.join(tempfile.gettempdir(), f"steelreuse_sap_{uuid.uuid4().hex}.sdb")
    if model.File.Save(save_path) != 0:
        return _fallback(demand_members, "SAP2000 File.Save failed — using analytic loads")
    model.Analyze.SetRunCaseFlag("", True, True)
    if model.Analyze.RunAnalysis() != 0:
        return _fallback(demand_members, "SAP2000 analysis failed — using analytic loads")

    model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    model.Results.Setup.SetComboSelectedForOutput(combo_name)

    flange_restrained = bool(getattr(loads, "beam_flange_restrained", True))
    demands_by_member: dict[str, list[tuple[str, MemberDemand]]] = {}
    for mid, (i, j) in topo.member_nodes.items():
        m = members_by_id[mid]
        ni, nj = topo.nodes[i], topo.nodes[j]
        length = math.dist((ni.x, ni.y, ni.z), (nj.x, nj.y, nj.z))
        forces = _read_frame_forces(model, mid, combo_name)
        restrained = flange_restrained if m.role == "beam" else False
        w_serv = _beam_service_udl(loads, mid) if m.role == "beam" else None
        ky = getattr(m, "ky", None) or 1.0
        kz = getattr(m, "kz", None) or 1.0
        demands_by_member[mid] = [(combo_name, MemberDemand(
            N_Ed=_governing_axial(forces, combo_name),
            My_Ed=_envelope_moment(forces, "My", combo_name),
            Mz_Ed=_envelope_moment(forces, "Mz", combo_name),
            Vz_Ed=_governing_shear(forces, combo_name),
            L=length, ky=ky, kz=kz,
            compression_flange_restrained=restrained, w_service=w_serv,
        ))]

    slots_by_member = _build_slots_by_member(
        demands_by_member, members_by_id, topo, loads, column_nodes, flange_restrained)
    return FrameResult(
        demands_by_member=demands_by_member,
        node_count=len(topo.nodes),
        member_count=len(topo.member_nodes),
        base_node_ids=topo.base_node_ids,
        skipped_member_ids=topo.skipped_member_ids,
        warnings=[f"solved with SAP2000 {_sap_version(model)} "
                  "(experimental OAPI backend, gravity only)"],
        ok=True,
        slots_by_member=slots_by_member,
    )


def _read_frame_forces(model, mid: str, combo_name: str) -> _SapMemberForces:
    """Read SAP2000 station forces for one frame object and wrap them for the extraction helpers.

    ``Results.FrameForce`` returns parallel station arrays (CSI OAPI byref → comtypes tuple, ret
    last): axial ``P`` (tension-positive), shears ``V2``/``V3``, torsion ``T``, moments ``M2``/``M3``.
    We keep only the stations belonging to ``combo_name``.
    """
    res = model.Results.FrameForce(
        mid, _ITEM_OBJECT_ELM, 0, [], [], [], [], [], [], [], [], [], [], [], [], [])
    # res = [NumberResults, Obj, ObjSta, Elm, ElmSta, LoadCase, StepType, StepNum,
    #        P, V2, V3, T, M2, M3, ret]
    number, _obj, _objsta, _elm, _elmsta, loadcase = res[0], res[1], res[2], res[3], res[4], res[5]
    P, V2, V3, _T, M2, M3 = res[8], res[9], res[10], res[11], res[12], res[13]
    rec: dict[str, list[float]] = {"P": [], "M3": [], "M2": [], "V2": [], "V3": []}
    components = (("P", P), ("M3", M3), ("M2", M2), ("V2", V2), ("V3", V3))
    for k in range(number):
        if loadcase[k] != combo_name:
            continue
        for key, arr in components:
            rec[key].append(arr[k])
    if not rec["P"]:                       # no stations for this combo → treat as zero forces
        rec = {key: [0.0] for key in rec}
    return _SapMemberForces({combo_name: rec})


def _sap_version(model) -> str:
    """SAP2000 version string (e.g. '27.1.0'), or 'unknown' — for provenance in the benchmark."""
    try:
        return str(model.GetVersion("", 0.0)[0])
    except Exception:  # noqa: BLE001 - provenance is best-effort, never fatal
        return "unknown"


def _beam_width(loads, mid: str) -> float:
    trib = (getattr(loads, "tributary_overrides", None) or {}).get(mid)
    return loads.beam_tributary_width_m if trib is None else trib


def _beam_service_udl(loads, mid: str) -> float | None:
    return loads.characteristic_area_kpa() * _beam_width(loads, mid) or None
