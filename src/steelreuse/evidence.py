"""Per-run **evidence package** — one signable file an engineer reviews instead of re-deriving.

Roadmap §1.1 (docs/ROADMAP_CERTIFIABLE_REUSE.md). A single machine-readable JSON that bundles, for
one matching run:

  * **every input** (donor + demand model files, with SHA-256 hashes), plus the *resolved* reclaimed
    stock and demand slots — so a reviewer needs nothing but this file;
  * the **versions/weights/factors** the run used (tool version, section-catalogue + carbon-factor
    hashes and values, the objective and every economic weight);
  * per-assignment **EN 1993 pass-evidence** — governing clause, utilisation, χ_LT, governing load
    combination, the section class / f_y the check ran at, and the carbon breakdown that produced the
    booked saving;
  * the **`verify_match` certificate** (feasible + no improving single move), computed at build time;
  * a **carbon reconciliation** proving the per-assignment savings sum to
    :attr:`MatchResult.total_co2_saved_kg`.

Nothing here is new science — every number is re-derived from the same kernels the solve used
(:func:`steelreuse.core.ec3_checks.check_member`, :func:`steelreuse.match.optimize.verify_match`,
:func:`steelreuse.match.optimize.baseline_new_mass_kg`). The package is *assembly + a stable schema*.

Round-trip guarantee: :func:`rebuild_run` reconstructs ``(supply, slots, result)`` from the
``reconstruction`` block exactly as the solve saw them, so :func:`verify_from_package` re-runs the
independent audit from the package alone and returns the same verdict.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .core import ec3_checks, rules
from .core.carbon import DEFAULT_FACTORS, CarbonFactor, load_factors
from .core.ec3_checks import MemberDemand, check_member
from .core.mismatch import mismatch_summary
from .core.sections import (
    DEFAULT_ANGLES_CATALOG,
    DEFAULT_CATALOG,
    DEFAULT_CHANNELS_CATALOG,
    DEFAULT_EU_CHS_CATALOG,
    DEFAULT_UK_CATALOG,
    DEFAULT_US_CATALOG,
    DEFAULT_US_HSS_CATALOG,
    DEFAULT_US_ROUND_CATALOG,
    SectionProps,
    load_default_catalog,
)
from .match.optimize import (
    Assignment,
    DemandSlot,
    MatchResult,
    SupplyItem,
    assignment_alternatives,
    baseline_new_mass_kg,
    verify_match,
)
from .pipeline import PipelineResult
from .schema import SCHEMA_VERSION

EVIDENCE_SCHEMA = "steelreuse-evidence/1"

# The data files whose content defines the rule basis of a run — hashed into the package so a reviewer
# can confirm the catalogue/factor tables match the ones the headline numbers were computed against.
_CATALOG_FILES = (
    DEFAULT_CATALOG, DEFAULT_US_CATALOG, DEFAULT_US_HSS_CATALOG, DEFAULT_UK_CATALOG,
    DEFAULT_US_ROUND_CATALOG, DEFAULT_EU_CHS_CATALOG, DEFAULT_CHANNELS_CATALOG, DEFAULT_ANGLES_CATALOG,
)

# Per-assignment carbon reconciliation tolerance (kg CO2e): the stored saving is rounded to 2 dp and
# summed; an independent recomputation must land within this of the stored figure (matches the
# score-drift tolerance verify_match uses).
_CO2_TOL_KG = 0.06


def _sha256(path: str | Path) -> str | None:
    """SHA-256 of a file's bytes, or ``None`` if it can't be read (so a missing input never crashes
    package generation — the absence is itself recorded)."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _factor_dict(f: CarbonFactor) -> dict:
    return {
        "a1a3_kgco2e_per_kg": f.a1a3,
        "reuse_process_kgco2e_per_kg": f.reuse_process,
        "recycle_credit_kgco2e_per_kg": f.recycle_credit,
        "reroll_credit_kgco2e_per_kg": f.reroll_credit,
        "saved_per_kg_kgco2e": round(f.saved_per_kg, 6),
        "source": f.source,
    }


# --------------------------------------------------------------------------------------------------
# Serialization of the run objects (round-trippable for re-checking)
# --------------------------------------------------------------------------------------------------

def _supply_to_dict(s: SupplyItem) -> dict:
    return dataclasses.asdict(s)


def _slot_to_dict(slot: DemandSlot) -> dict:
    """Serialize a slot WITH its full load-combination envelope, so the re-check sees every
    combination the matcher verified the donor against — not just the gravity case."""
    return {
        "id": slot.id,
        "member_id": slot.member_id,
        "role": slot.role,
        "required_length_mm": slot.required_length_mm,
        "grade": slot.grade,
        "design_section": slot.design_section,
        "combinations": [
            {"name": name, "demand": dataclasses.asdict(demand)}
            for name, demand in slot.combinations
        ],
    }


def _result_to_dict(result: MatchResult) -> dict:
    return {
        "assignments": [dataclasses.asdict(a) for a in result.assignments],
        "unmatched_slots": list(result.unmatched_slots),
        "unused_supply": list(result.unused_supply),
        "solver_status": result.solver_status,
        "weights": dict(result.weights),
        "donor_leftover_mm": dict(result.donor_leftover_mm),
    }


def _supply_from_dict(d: dict) -> SupplyItem:
    return SupplyItem(**d)


def _slot_from_dict(d: dict) -> DemandSlot:
    combos = [(c["name"], MemberDemand(**c["demand"])) for c in d["combinations"]]
    return DemandSlot(
        id=d["id"], member_id=d["member_id"], role=d["role"],
        required_length_mm=d["required_length_mm"],
        demand=combos[0][1], demands=combos,
        grade=d.get("grade"), design_section=d.get("design_section"),
    )


def _result_from_dict(d: dict) -> MatchResult:
    return MatchResult(
        assignments=[Assignment(**a) for a in d["assignments"]],
        unmatched_slots=list(d["unmatched_slots"]),
        unused_supply=list(d["unused_supply"]),
        solver_status=d["solver_status"],
        weights=dict(d["weights"]),
        donor_leftover_mm={k: float(v) for k, v in d["donor_leftover_mm"].items()},
    )


def rebuild_run(package: dict) -> tuple[list[SupplyItem], list[DemandSlot], MatchResult]:
    """Reconstruct ``(supply, slots, result)`` from a package's ``reconstruction`` block, exactly as
    the solve saw them — the substrate for an independent re-check (see :func:`verify_from_package`)."""
    r = package["reconstruction"]
    supply = [_supply_from_dict(s) for s in r["supply"]]
    slots = [_slot_from_dict(s) for s in r["slots"]]
    result = _result_from_dict(r["result"])
    return supply, slots, result


def verify_from_package(
    package: dict,
    catalog: dict[str, SectionProps] | None = None,
    factors: dict[str, CarbonFactor] | None = None,
) -> list[str]:
    """Re-run the independent :func:`verify_match` audit from the package alone; ``[]`` == verified.

    Uses the default section catalogue (whose hashes are recorded in the package, so a reviewer can
    confirm it is the same table) unless one is supplied. This is the acceptance check: the verdict
    must match the certificate the package was built with.
    """
    supply, slots, result = rebuild_run(package)
    catalog = catalog or load_default_catalog()
    return verify_match(supply, slots, catalog, result, factors)


# --------------------------------------------------------------------------------------------------
# Per-assignment EN 1993 evidence + carbon breakdown
# --------------------------------------------------------------------------------------------------

def _governing_demand(slot: DemandSlot, name: str) -> tuple[str, MemberDemand]:
    """The (name, demand) of the assignment's governing combination, falling back to the worst-named
    or first combination if the stored name isn't found (defensive — it always should be)."""
    for n, d in slot.combinations:
        if n == name:
            return n, d
    return slot.combinations[0]


def _assignment_evidence(
    a: Assignment,
    slot: DemandSlot,
    sup: SupplyItem,
    catalog: dict[str, SectionProps],
    factor: CarbonFactor,
    weights: dict,
) -> dict:
    """EN 1993 pass-evidence + carbon breakdown for one assignment, re-derived from the kernels.

    The EN block re-runs :func:`check_member` on the governing combination so the package carries the
    governing clause, every sub-check's utilisation/detail, the section class and f_y the check ran
    at, and the warnings. The carbon block re-derives the booked saving from the same avoided-new
    baseline the matcher used and reconciles it against the stored figure.
    """
    sec = catalog.get(a.section)
    name, demand = _governing_demand(slot, a.governing_combination)
    grade = sup.grade or "S235"
    en: dict = {"available": False}
    if sec is not None:
        chk = check_member(sec, grade, demand, sup.knockdown)
        en = {
            "available": True,
            "governing_combination": name,
            "governing_clause": chk.governing,
            "utilization": round(chk.utilization, 4),
            "status": chk.status,
            "section_class": chk.section_class,
            "f_y_Nmm2": round(chk.fy, 1),
            "knockdown": sup.knockdown,
            "design_actions": dataclasses.asdict(demand),
            "checks": [
                {"name": c.name, "utilization": round(c.utilization, 4), "detail": c.detail}
                for c in chk.checks
            ],
            "warnings": chk.warnings,
        }

    # Carbon breakdown: same avoided-new basis as match._feasible_cell, re-derived from the catalogue.
    carbon: dict = {"available": False}
    if sec is not None:
        used_len = slot.required_length_mm
        mass_used = sec.mass_kgm * used_len / 1000.0
        baseline_mass = baseline_new_mass_kg(slot, catalog, weights.get("new_build_grade", "S355"))
        avoided_basis = baseline_mass if baseline_mass is not None else mass_used
        avoided_new = avoided_basis * factor.a1a3
        reuse_process_cost = mass_used * factor.reuse_process
        connection_penalty = weights.get("connection_penalty_kg", 5.0)
        cf_credit = weights.get("counterfactual_credit", 0.0)
        counterfactual_cost = mass_used * cf_credit
        net = avoided_new - reuse_process_cost - connection_penalty - counterfactual_cost
        carbon = {
            "available": True,
            "used_length_mm": round(used_len, 1),
            "mass_used_kg": round(mass_used, 2),
            "avoided_new_mass_kg": round(avoided_basis, 2),
            "avoided_new_kgco2e": round(avoided_new, 2),
            "reuse_process_kgco2e": round(reuse_process_cost, 2),
            "connection_refab_kgco2e": round(connection_penalty, 2),
            "counterfactual_credit_kgco2e": round(counterfactual_cost, 2),
            "net_co2_saved_kgco2e_recomputed": round(net, 2),
            "net_co2_saved_kgco2e_stored": a.co2_saved_kg,
            "reconciles": abs(net - a.co2_saved_kg) <= _CO2_TOL_KG,
        }

    return {
        "supply_id": a.supply_id,
        "slot_id": a.slot_id,
        "section": a.section,
        "design_section": slot.design_section,
        "grade": grade,
        "status": a.status,
        "utilization": a.utilization,
        "chi_LT": a.chi_lt,
        "chi_LT_if_unrestrained": a.chi_lt_if_free,
        "offcut_mm": a.offcut_mm,
        "connection_status": a.connection_status,
        "connection_note": a.connection_note,
        "en_1993": en,
        "carbon": carbon,
    }


# --------------------------------------------------------------------------------------------------
# Package assembly
# --------------------------------------------------------------------------------------------------

def build_evidence_package(
    res: PipelineResult,
    donor_path: str | None = None,
    demand_paths: list[str] | str | None = None,
    run_context: dict | None = None,
    catalog: dict[str, SectionProps] | None = None,
    factors: dict[str, CarbonFactor] | None = None,
) -> dict:
    """Assemble the per-run evidence package (a JSON-serialisable dict) from a finished pipeline run.

    ``run_context`` is an optional free-form dict of run metadata the CLI fills (command line, load
    model, National Annex, …) — it is recorded verbatim under ``run.context``. ``catalog``/``factors``
    default to the same tables the pipeline used, so the re-derived evidence matches the solve.
    """
    catalog = catalog or load_default_catalog()
    factors = factors or load_factors()
    factor = factors["steel"]
    m = res.match
    weights = dict(m.weights or {})

    sup_by_id = {s.id: s for s in res.supply}
    slot_by_id = {s.id: s for s in res.slots}

    # The verify_match certificate, computed now over the same objects the solve produced.
    issues = verify_match(res.supply, res.slots, catalog, m, factors)
    # Per-assignment next-best alternative (Tier 3), re-derived from the same inputs.
    alternatives = assignment_alternatives(res.supply, res.slots, catalog, m, factors)

    assignments_evidence = []
    for a in m.assignments:
        slot = slot_by_id.get(a.slot_id)
        sup = sup_by_id.get(a.supply_id)
        if slot is None or sup is None:
            # Should never happen for a coherent result; record the gap rather than crash.
            assignments_evidence.append({
                "supply_id": a.supply_id, "slot_id": a.slot_id, "section": a.section,
                "en_1993": {"available": False, "note": "donor or slot not found in run inputs"},
                "carbon": {"available": False},
            })
            continue
        assignments_evidence.append(
            _assignment_evidence(a, slot, sup, catalog, factor, weights))

    sum_assignment_co2 = round(sum(a.co2_saved_kg for a in m.assignments), 2)
    all_carbon_reconcile = all(
        ev["carbon"].get("reconciles", False)
        for ev in assignments_evidence if ev["carbon"].get("available")
    )

    demand_list = ([demand_paths] if isinstance(demand_paths, str)
                   else list(demand_paths) if demand_paths else [])

    # Donor-row mismatch log (Roadmap §1.2): accounts for 100% of donor rows with a reason each.
    mlog = list(getattr(res, "mismatch_log", None) or [])

    package = {
        "schema": EVIDENCE_SCHEMA,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool": {
            "name": "steelreuse",
            "version": __version__,
            "extraction_schema_version": SCHEMA_VERSION,
        },
        "run": {
            "objective": m.objective,
            "solver_status": m.solver_status,
            "proven_optimal": m.proven_optimal,
            "weights": weights,
            "counterfactual": weights.get("counterfactual", "none"),
            "ruleset_version": rules.RULESET_VERSION,
            "context": run_context or {},
        },
        # Externalised, version-stamped rule data the run used (grades, grade defaults, condition /
        # verification knockdowns, carbon factors, section catalogue): versions, sources, hashes.
        "rules": rules.rules_manifest(),
        "inputs": {
            "donor": {
                "path": str(donor_path) if donor_path else None,
                "sha256": _sha256(donor_path) if donor_path else None,
                "model_name": res.donor.model_name if res.donor else "",
                "n_members": len(res.donor.members) if res.donor else None,
                "supply_count": res.supply_count,
            },
            "demand": [
                {"path": str(p), "sha256": _sha256(p)} for p in demand_list
            ],
            "slot_count": res.slot_count,
            "catalog": {
                "files": [
                    {"name": Path(p).name, "sha256": _sha256(p)}
                    for p in _CATALOG_FILES
                ],
            },
            "carbon_factors": {
                "path": Path(DEFAULT_FACTORS).name,
                "sha256": _sha256(DEFAULT_FACTORS),
                "steel": _factor_dict(factor),
            },
        },
        # The EN 1993 partial factors + elastic constants the checks ran at — so a hand re-check uses
        # the same gamma/E/G values the tool did, not a different National Annex's.
        "en_constants": {
            "gamma_M0": ec3_checks.GAMMA_M0,
            "gamma_M1": ec3_checks.GAMMA_M1,
            "E_Nmm2": ec3_checks.E_STEEL,
            "G_Nmm2": ec3_checks.G_STEEL,
        },
        "headline": {
            "n_reused": m.n_reused,
            "slot_count": res.slot_count,
            "co2_saved_kg": round(m.total_co2_saved_kg, 2),
            "offcut_mm": round(m.total_offcut_mm, 1),
            "donor_stock_potential_kgco2e": round(res.passport.total_saved_kgco2e, 1),
        },
        "assignments": assignments_evidence,
        "certificate": {
            "verify_match_issues": issues,
            "verified": not issues,
            "proven_optimal": m.proven_optimal,
            "solver_status": m.solver_status,
            "objective": m.objective,
        },
        "carbon_reconciliation": {
            "sum_assignment_co2_saved_kg": sum_assignment_co2,
            "match_total_co2_saved_kg": round(m.total_co2_saved_kg, 2),
            "reconciles": abs(sum_assignment_co2 - round(m.total_co2_saved_kg, 2)) <= _CO2_TOL_KG,
            "per_assignment_reconciles": all_carbon_reconcile,
            "basis": (f"avoided-new (A1-A3 {factor.a1a3} kgCO2e/kg) minus reuse process "
                      f"({factor.reuse_process} kgCO2e/kg) minus connection refab and any "
                      f"counterfactual credit; objective = {m.objective}"),
        },
        # Donor-row provenance: every donor member classified (mapped / fuzzy / unknown / quarantined)
        # with a reason — 100% coverage so nothing is silently dropped (Roadmap §1.2).
        "mismatch_log": mlog,
        "mismatch_summary": mismatch_summary(mlog),
        # Demand-side WHY: the binding constraint, its lever, and a per-unfilled-slot reason (Tier 1).
        "diagnosis": getattr(res, "diagnosis", None) or {},
        # Per-assignment WHY: the runner-up donor for each filled slot + the net-CO2 margin (Tier 3).
        "assignment_alternatives": alternatives,
        # Everything needed to re-run verify_match from the package alone (see rebuild_run).
        "reconstruction": {
            "supply": [_supply_to_dict(s) for s in res.supply],
            "slots": [_slot_to_dict(s) for s in res.slots],
            "result": _result_to_dict(m),
        },
    }
    # Per-unused-donor end-of-fate + WHY-unused reason (Tier 2), when the run computed it.
    disposition = getattr(res, "disposition", None)
    if disposition is not None:
        package["stock_disposition"] = disposition
    # Per-donor what-if marginal value (Tier 4), when the run re-solved it (opt-in).
    marginal_value = getattr(res, "marginal_value", None)
    if marginal_value is not None:
        package["donor_marginal_value"] = marginal_value
    return package


def write_evidence_package(package: dict, path: str | Path) -> Path:
    """Write the package as pretty-printed JSON, creating parent directories. Returns the path."""
    import json

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(package, indent=2), encoding="utf-8")
    return p
