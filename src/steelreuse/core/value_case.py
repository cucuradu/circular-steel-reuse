"""Reuse value + suitability per donor member — the prize the shredder hides.

No demand model needed. Given a donor inventory, for each reusable member it reports:
  * the steel's value as **reclaimed structural steel** vs as **scrap** (the reuse *premium* —
    the upside that is invisible when everything goes to the shredder), and
  * the **embodied CO2** saved by reusing instead of buying new, and
  * a **reuse-suitability verdict** (REUSE / REVIEW / SCRAP) driven by mapping + the
    pre-demolition audit (grade verification + condition), per SCI P427.

Deliberately scope-limited to the *steel value* and its *reliability*. The cost of
deconstruction (soft-strip of architectural finishes and services, scaffolding, crane time,
asbestos) dwarfs the steel and is not visible from a structural model, so this tool does NOT
net it off — it quantifies the prize and the reliability, and leaves the contractor to weigh
that against their own deconstruction estimate. (An experimental steel-only labour sketch lives
in :mod:`steelreuse.core.labour`, not wired into the verdict.)
"""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditDecision, assess_member
from .carbon import CarbonFactor, load_factors, member_mass_kg
from .deconstruction import effective_recoverable_length
from .sections import SectionProps, load_default_catalog, resolve_members

# Grade verification bases strong enough to rely on the nominal f_y without a fresh test (SCI P427:
# reclaimed steel may be relied on only when its grade is established by mill certificate or coupon
# test). Anything weaker -> REVIEW: usable, but coupon-test it before structural reuse.
_TEST_VERIFIED = {"mill_cert", "coupon_tested"}


@dataclass(frozen=True)
class MarketParams:
    scrap_price_per_tonne: float = 240.0      # GBP/t (MEPS UK HMS 1&2, 2024 avg)
    reclaimed_price_per_tonne: float = 950.0  # GBP/t (BCSA + UK reclaim yards, ~70% of new S355)
    co2_price_per_tonne: float = 0.0          # GBP/tCO2e; 0 = off; ~75 = UK ETS 2024 avg


@dataclass
class MemberValueCase:
    id: str
    section: str | None
    grade: str | None
    length_mm: float
    mass_kg: float
    scrap_value_gbp: float       # value weighed in as scrap
    reclaimed_value_gbp: float   # value sold as reclaimed structural steel (0 if not reusable)
    reuse_premium_gbp: float     # reclaimed - scrap: the upside of reuse over the shredder
    co2_saved_kg: float          # embodied CO2 avoided vs buying the section new
    co2_value_gbp: float         # co2_saved priced at MarketParams.co2_price_per_tonne (0 if off)
    reuse_score: float           # 0..1 reuse-potential heuristic (standardization x length)
    verification_status: str     # surveyed grade basis ("" if un-audited)
    condition_grade: str         # surveyed condition A-D ("" if un-audited)
    verdict: str                 # "REUSE" | "REVIEW" | "SCRAP"
    note: str                    # plain-language reason + action
    audit_admitted: bool
    audit_reason: str


@dataclass
class ValueCaseResult:
    rows: list[MemberValueCase]
    params: MarketParams
    reuse_count: int             # reuse-ready (verified, sound)
    review_count: int            # reusable but needs verification/inspection first
    scrap_count: int             # not reusable (recycle only)
    reusable_mass_kg: float      # mass over REUSE + REVIEW
    scrap_mass_kg: float         # mass over SCRAP rows
    total_reclaimed_value_gbp: float   # REUSE + REVIEW
    total_reuse_premium_gbp: float     # REUSE + REVIEW: the prize vs shredding it all
    total_co2_saved_kg: float          # REUSE + REVIEW
    skipped_total: int = 0             # members not assessed at all (excluded from rows)
    skipped_breakdown: dict[str, int] | None = None  # {"foundation": n, "unmapped": n}


# Family/category name hints for elements that are never reusable steel members (skipped, not scrapped).
_FOUNDATION_HINTS = ("foundation", "footing", "pile", "raft", "pad ", "pilecap", "grade beam")


def _skip_reason(member, has_section: bool) -> str | None:
    """Why a member should be excluded from the list entirely, or None to assess it.

    Foundations (concrete footings/piles miscategorised as framing) are never reusable steel and are
    always skipped. Unmapped members (open-web joists like the K-series, plates, proprietary items)
    are skipped by default because the tool cannot value or check them — listing them as SCRAP is noise.
    """
    blob = ((getattr(member, "category", "") or "") + " "
            + (getattr(member, "raw_section", "") or "")).lower()
    if any(h in blob for h in _FOUNDATION_HINTS):
        return "foundation"
    if not has_section:
        return "unmapped"
    return None


def _verdict(admitted: bool, has_section: bool, verification: str, condition: str) -> str:
    """Reuse-suitability classification (no economics — the premium is shown either way).

    SCRAP  — not mapped, or quarantined by the audit (condition D, unverified, below knockdown floor).
    REVIEW — mapped and admitted, but the grade is not test-verified (mill cert / coupon) or the
             condition is C: reusable, but it must be verified / inspected before structural reliance.
    REUSE  — mapped, admitted, grade test-verified, condition sound.
    """
    if not admitted or not has_section:
        return "SCRAP"
    if verification not in _TEST_VERIFIED or condition.upper() == "C":
        return "REVIEW"
    return "REUSE"


def _note(verdict: str, reclaimed: float, scrap: float, premium: float, co2: float,
          verification: str, condition: str, audit_reason: str, raw_section: str) -> str:
    """Plain-language reason + the action an engineer should take."""
    if verdict == "REUSE":
        return (f"Reuse-ready: grade {verification or 'verified'}, sound condition. Worth GBP "
                f"{reclaimed:.0f} reclaimed (GBP {premium:.0f} more than scrap); saves {co2:.0f} kg CO2.")
    if verdict == "REVIEW":
        cond = condition.upper()
        if cond == "C":
            why = "condition C (possible section loss/deformation) — inspect before reuse"
        elif not verification:
            why = "no grade documentation — coupon-test to establish grade before structural reuse"
        else:
            why = (f"grade basis '{verification}' not test-verified — coupon-test before structural "
                   f"reuse (SCI P427)")
        return (f"Reusable pending check: {why}. Worth GBP {reclaimed:.0f} reclaimed "
                f"(GBP {premium:.0f} over scrap); saves {co2:.0f} kg CO2.")
    if raw_section:
        return f"Section not recognised ({raw_section}); cannot value or check — recycle."
    return f"Not reusable ({audit_reason or 'failed audit'}); scrap value GBP {scrap:.0f} only."


def value_case(
    donor,
    catalog: dict[str, SectionProps] | None = None,
    params: MarketParams | None = None,
    factors: dict[str, CarbonFactor] | None = None,
    knockdown: float = 1.0,
    include_unverified: bool = False,
    include_unmapped: bool = False,
) -> ValueCaseResult:
    """Per-member reuse value + suitability for a donor model (no demand model required).

    Members the tool cannot meaningfully assess are *excluded* from the rows (and counted in
    ``skipped_breakdown``) so the list stays actionable: foundations (concrete footings/piles that
    arrive miscategorised as framing) are always skipped, and unmapped sections (open-web joists
    such as the K-series, plates, proprietary items) are skipped unless ``include_unmapped`` is set.
    Mapped members that fail the pre-demolition audit stay in the list as SCRAP — they are real
    steel with a scrap value, just not reusable.
    """
    if catalog is None:
        catalog = load_default_catalog()
    if params is None:
        params = MarketParams()
    if factors is None:
        factors = load_factors()

    # Map raw_section -> section in place, exactly as the pipeline does before matching. The
    # extractor fills only raw_section (the Revit type name) and leaves section=None, so without
    # this every member would look unmapped. resolve_members re-derives section from raw_section.
    resolve_members(donor.members, catalog)

    default_factor = factors.get("steel") or next(iter(factors.values()))

    # Standardization/length reuse-potential heuristic (stdlib; ml extra not required). A genuine
    # import failure (ml package broken) degrades to 0 rather than aborting the whole business case.
    try:
        from ..ml.reuse_score import reuse_scores
        scores = reuse_scores(donor.members)
    except ImportError:
        scores = {}

    def _scrap_gbp(mass_kg: float) -> float:
        return round(mass_kg / 1000.0 * params.scrap_price_per_tonne, 2)

    skipped: dict[str, int] = {}
    rows: list[MemberValueCase] = []
    for m in donor.members:
        sec: SectionProps | None = catalog.get(m.section) if m.section else None

        # Exclude members the tool should not assess (foundations always; unmapped by default).
        reason = _skip_reason(m, has_section=sec is not None)
        if reason == "foundation" or (reason == "unmapped" and not include_unmapped):
            skipped[reason] = skipped.get(reason, 0) + 1
            continue

        audit: AuditDecision = assess_member(m, default_knockdown=knockdown,
                                             include_unverified=include_unverified)
        usable_length = effective_recoverable_length(m)
        mass_kg = member_mass_kg(sec, usable_length) if sec is not None else 0.0
        score = scores.get(m.id, 0.0)
        verification, condition = audit.verification, audit.condition

        verdict = _verdict(audit.admitted, sec is not None, verification, condition)

        # Reuse value only accrues to members that can actually be reused (REUSE / REVIEW).
        reusable = verdict in ("REUSE", "REVIEW")
        scrap_value = _scrap_gbp(mass_kg)
        if reusable:
            reclaimed_value = round(mass_kg / 1000.0 * params.reclaimed_price_per_tonne, 2)
            grade_key = (m.material_grade or "").strip().lower()
            factor = factors.get(grade_key) or default_factor
            co2_saved = round(mass_kg * factor.saved_per_kg, 2)
        else:
            reclaimed_value = 0.0
            co2_saved = 0.0
        premium = round(reclaimed_value - scrap_value, 2) if reusable else 0.0
        co2_value = round(co2_saved / 1000.0 * params.co2_price_per_tonne, 2)

        rows.append(MemberValueCase(
            id=m.id, section=m.section, grade=m.material_grade,
            length_mm=round(usable_length, 1), mass_kg=round(mass_kg, 2),
            scrap_value_gbp=scrap_value, reclaimed_value_gbp=reclaimed_value,
            reuse_premium_gbp=premium, co2_saved_kg=co2_saved, co2_value_gbp=co2_value,
            reuse_score=score, verification_status=verification, condition_grade=condition,
            verdict=verdict,
            note=_note(verdict, reclaimed_value, scrap_value, premium, co2_saved,
                       verification, condition, audit.reason,
                       "" if sec is not None else (m.raw_section or "?")),
            audit_admitted=audit.admitted, audit_reason=audit.reason))

    # Rank by the prize (reuse premium), so the most valuable recoveries are at the top.
    rows.sort(key=lambda r: r.reuse_premium_gbp, reverse=True)

    reuse = [r for r in rows if r.verdict == "REUSE"]
    review = [r for r in rows if r.verdict == "REVIEW"]
    scrap = [r for r in rows if r.verdict == "SCRAP"]
    reusable_rows = reuse + review

    return ValueCaseResult(
        rows=rows,
        params=params,
        reuse_count=len(reuse),
        review_count=len(review),
        scrap_count=len(scrap),
        reusable_mass_kg=round(sum(r.mass_kg for r in reusable_rows), 2),
        scrap_mass_kg=round(sum(r.mass_kg for r in scrap), 2),
        total_reclaimed_value_gbp=round(sum(r.reclaimed_value_gbp for r in reusable_rows), 2),
        total_reuse_premium_gbp=round(sum(r.reuse_premium_gbp for r in reusable_rows), 2),
        total_co2_saved_kg=round(sum(r.co2_saved_kg for r in reusable_rows), 2),
        skipped_total=sum(skipped.values()),
        skipped_breakdown=skipped,
    )
