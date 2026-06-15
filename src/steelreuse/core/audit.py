"""Pre-demolition audit (PDA) layer: turn a surveyed donor inventory into trustworthy supply.

A *pre-demolition audit* is the survey carried out on a building slated for demolition or deep
refurbishment, before anything is taken down, to inventory its materials and record — per member —
the **physical condition** and the **basis on which the material grade can be trusted**. It is the
upstream process whose deliverable is the donor model this tool consumes; regulatory drivers include
the EU Construction & Demolition Waste Management Protocol, France's mandatory *Diagnostic PEMD*, the
EU *Level(s)* framework, and Italy's *CAM Edilizia*. Reuse-specific guidance (e.g. SCI **P427**) makes
clear that reclaimed steel may only be relied on when its grade is verified (mill certificate or
coupon test) and its condition is sound.

This module converts those two audit facts into the numbers the deterministic EN 1993 check already
understands:

  * a per-member **knockdown** on f_y (condition / verification uncertainty), and
  * a **quarantine** decision — unverified or unsuitable stock is excluded from the certified supply
    exactly the way an unmapped or fuzzy-matched section is, so it can never silently enter analysis.

Design rule (keeps existing results byte-identical): a member that carries **no** audit data at all is
treated as legacy input — admitted to supply at the run's default knockdown. Quarantine and derived
knockdowns engage **only** when the audit actually recorded something. Honest by default
(docs/DESIGN_PRINCIPLES.md Hard Rule #5): the tool never *invents* a condition; absence of data means
"not audited", not "fine".

Standard library only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

# --- Verification basis -> how far the declared grade can be trusted ------------------------------
# Knockdown applied to f_y purely for *grade* uncertainty (independent of physical condition). A full
# mill certificate or a coupon test is the only basis on which the nominal f_y is taken at face value;
# weaker bases attract a small reduction, and an unverified member is quarantined rather than derated.
VERIFICATION_KNOCKDOWN = {
    "mill_cert": 1.00,      # original mill certificate / full traceability
    "coupon_tested": 1.00,  # sampled and tensile-tested -> grade established by test
    "documented": 0.95,     # design drawings / records state the grade, but no cert or test
    "visual_only": 0.90,    # grade only assumed from era/appearance; conservative reduction
    "unverified": 0.00,     # no basis at all -> quarantined (factor unused; see assess_member)
}
# Verification bases that are sufficient for a member to enter the certified supply.
ACCEPTED_VERIFICATION = {"mill_cert", "coupon_tested", "documented", "visual_only"}

# --- Surveyed physical condition -> knockdown ----------------------------------------------------
# A coarse A-D condition scale (common in reuse surveys). Grades A-C apply a knockdown reflecting
# corrosion / section loss / minor deformation; grade D is structurally unsuitable and is excluded.
CONDITION_KNOCKDOWN = {
    "A": 1.00,   # as-new: negligible corrosion, no deformation or damage
    "B": 0.95,   # minor: light surface corrosion, cosmetic only
    "C": 0.85,   # significant: measurable section loss / minor deformation -> design knockdown
    "D": 0.00,   # unsuitable: heavy corrosion, distortion, or damage -> not reusable (quarantined)
}
REJECT_CONDITION = {"D"}

MIN_KNOCKDOWN = 0.30  # floor so a typo can never zero-out f_y silently; below this -> quarantine


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def has_audit_data(member) -> bool:
    """True if the member carries *any* pre-demolition-audit field (so quarantine logic applies)."""
    return bool(
        _norm(getattr(member, "condition_grade", None))
        or _norm(getattr(member, "verification_status", None))
        or getattr(member, "knockdown", None) is not None
        or _norm(getattr(member, "defects", ""))
        or getattr(member, "recoverable_length_mm", None) is not None
    )


def recoverable_length(member) -> float:
    """Usable length after de-construction. Falls back to the physical length when not surveyed."""
    rl = getattr(member, "recoverable_length_mm", None)
    if rl is not None and rl > 0:
        return float(rl)
    return float(getattr(member, "length_mm", 0.0) or 0.0)


@dataclass
class AuditDecision:
    """The audit verdict for one donor member."""

    member_id: str
    admitted: bool          # may this member enter the certified supply?
    knockdown: float        # f_y factor to apply if admitted
    condition: str          # normalized condition grade ("" if none)
    verification: str       # normalized verification basis ("" if none)
    audited: bool           # did the member carry any audit data at all?
    reason: str = ""        # why it was quarantined (empty if admitted)


def assess_member(
    member,
    default_knockdown: float = 1.0,
    include_unverified: bool = False,
) -> AuditDecision:
    """Decide whether a donor member is admitted to supply, and at what knockdown.

    Precedence for the knockdown:
      1. an explicit ``knockdown`` set by the auditor on the member (overrides everything);
      2. otherwise the product of the condition and verification factors;
      3. otherwise (no audit data) the run's ``default_knockdown`` (the CLI ``--knockdown``).

    Quarantine (``admitted = False``) engages only when audit data is present and says the member is
    unverified (and ``include_unverified`` is off) or in an unsuitable condition. ``include_unverified``
    lets a caller knowingly admit unverified stock (at a conservative knockdown) — never the default.
    """
    cond = _norm(getattr(member, "condition_grade", None)).upper()  # A/B/C/D
    ver = _norm(getattr(member, "verification_status", None))
    audited = has_audit_data(member)
    mid = getattr(member, "id", "?")

    # Legacy / un-audited member: behave exactly as before — admit at the run default.
    if not audited:
        return AuditDecision(mid, True, default_knockdown, "", "", False)

    # Hard rejections from the survey itself.
    if cond in REJECT_CONDITION:
        return AuditDecision(mid, False, 1.0, cond, ver, True,
                             reason=f"condition {cond} (unsuitable for reuse)")
    if ver and ver not in ACCEPTED_VERIFICATION and not include_unverified:
        return AuditDecision(mid, False, 1.0, cond, ver, True,
                             reason=f"grade {ver or 'unverified'} (no acceptable verification basis)")
    if not ver and not include_unverified and getattr(member, "knockdown", None) is None:
        # Has condition/defects data but no verification basis recorded -> treat as unverified.
        return AuditDecision(mid, False, 1.0, cond, ver, True,
                             reason="no verification basis recorded")

    # Admitted: resolve the knockdown.
    explicit = getattr(member, "knockdown", None)
    if explicit is not None:
        kd = float(explicit)
    else:
        cond_f = CONDITION_KNOCKDOWN.get(cond.upper(), 1.0) if cond else 1.0
        ver_f = VERIFICATION_KNOCKDOWN.get(ver, 0.90 if ver else 1.0)
        kd = cond_f * ver_f
        if ver == "unverified" and include_unverified:
            kd = min(kd if kd > 0 else 1.0, 0.85)  # knowingly admitted unverified -> conservative

    if kd < MIN_KNOCKDOWN:
        return AuditDecision(mid, False, kd, cond, ver, True,
                             reason=f"knockdown {kd:.2f} below floor {MIN_KNOCKDOWN:.2f}")
    return AuditDecision(mid, True, min(kd, 1.0), cond, ver, True)


_AUDIT_FIELDS = (
    "condition_grade", "verification_status", "knockdown", "recoverable_length_mm", "defects")


def load_audit_csv(path: str | Path) -> dict[str, dict]:
    """Load a pre-demolition-audit CSV keyed by member ``id``.

    This is the template an auditor fills in (one row per surveyed member). Columns: ``id`` plus any of
    ``condition_grade, verification_status, knockdown, recoverable_length_mm, defects``. Numeric columns
    are parsed; blank cells are ignored so a partially-filled survey is fine. The values are merged onto
    the donor members by id (see :func:`apply_audit`), so the audit can live alongside the BIM export
    rather than in it.
    """
    out: dict[str, dict] = {}
    p = Path(path)
    with p.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mid = (row.get("id") or "").strip()
            if not mid or mid.startswith("#"):  # blank or comment row (templates carry # comments)
                continue
            rec: dict = {}
            for fld in _AUDIT_FIELDS:
                val = (row.get(fld) or "").strip()
                if not val:
                    continue
                if fld in ("knockdown", "recoverable_length_mm"):
                    try:
                        rec[fld] = float(val)
                    except ValueError:
                        continue
                else:
                    rec[fld] = val
            if rec:
                out[mid] = rec
    return out


def apply_audit(members, by_id: dict[str, dict]) -> int:
    """Merge audit records (from :func:`load_audit_csv`) onto donor members by id, in place.

    Returns the number of members updated. Only the audit fields are touched; a member absent from the
    CSV is left as-is. An existing value on the member is overwritten by the CSV (the audit is the
    authority on condition/verification).
    """
    n = 0
    for m in members:
        rec = by_id.get(getattr(m, "id", None))
        if not rec:
            continue
        for fld, val in rec.items():
            setattr(m, fld, val)
        n += 1
    return n


@dataclass
class AuditSummary:
    """Aggregate provenance for a run, for the report and console."""

    decisions: dict[str, AuditDecision] = field(default_factory=dict)
    n_audited: int = 0          # donor members carrying any audit data
    n_admitted: int = 0         # admitted to supply
    n_quarantined: int = 0      # excluded by the audit (unverified / unsuitable / below floor)
    quarantined: list[tuple[str, str]] = field(default_factory=list)  # (member_id, reason)
    verification_counts: dict[str, int] = field(default_factory=dict)
    condition_counts: dict[str, int] = field(default_factory=dict)

    @property
    def present(self) -> bool:
        """Whether any audit data was supplied at all (drives whether the report shows the section)."""
        return self.n_audited > 0

    @property
    def avg_knockdown(self) -> float:
        """Mean knockdown across admitted, audited members (1.0 if none were audited)."""
        kds = [d.knockdown for d in self.decisions.values() if d.admitted and d.audited]
        return round(sum(kds) / len(kds), 3) if kds else 1.0


def assess_supply(
    members,
    default_knockdown: float = 1.0,
    include_unverified: bool = False,
) -> AuditSummary:
    """Run :func:`assess_member` over every donor member and aggregate the provenance."""
    summary = AuditSummary()
    for m in members:
        d = assess_member(m, default_knockdown, include_unverified)
        summary.decisions[d.member_id] = d
        if not d.audited:
            continue
        summary.n_audited += 1
        if d.verification:
            summary.verification_counts[d.verification] = (
                summary.verification_counts.get(d.verification, 0) + 1)
        if d.condition:
            summary.condition_counts[d.condition.upper()] = (
                summary.condition_counts.get(d.condition.upper(), 0) + 1)
        if d.admitted:
            summary.n_admitted += 1
        else:
            summary.n_quarantined += 1
            summary.quarantined.append((d.member_id, d.reason))
    return summary
