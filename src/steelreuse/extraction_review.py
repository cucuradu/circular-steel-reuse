# src/steelreuse/extraction_review.py
"""Extraction review: per-member data-quality + audit diagnostics.

The single source of truth for the problem report (data issues) and the PDA QA report (audit
coverage). Pure: standard library + steelreuse.core only (no Revit, no LLM), so it is fully
unit-tested and runs inside the headless engine the pyRevit panel shells out to. It reuses the
mapping layer (steelreuse.core.sections) and the audit layer (steelreuse.core.audit) verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core.audit import assess_supply
from .core.sections import map_section, resolve_members

SEVERITY_ORDER = {"error": 3, "warn": 2, "info": 1}

# Pre-match issue taxonomy. Class-4 / post-check status is NOT here -- it stays in the post-match
# results view (it needs a verification run, which review deliberately does not do).
ISSUE_SEVERITY = {
    "UNKNOWN_SECTION": "error",
    "FUZZY_MATCH": "warn",
    "MISSING_GRADE": "warn",
    "NO_COORDS": "info",
    "NOT_AUDITED": "info",
    "QUARANTINED_UNVERIFIED": "warn",
    "QUARANTINED_CONDITION_D": "warn",
    "LOW_KNOCKDOWN": "warn",
}

ISSUE_LEVER = {
    "UNKNOWN_SECTION": "section not recognised; rename the type or add a mapping override",
    "FUZZY_MATCH": "near-miss name; confirm via override CSV or fix the type name",
    "MISSING_GRADE": "material grade missing; a shape-family default is assumed (flagged)",
    "NO_COORDS": "no coordinates; this member cannot enter the frame analysis",
    "NOT_AUDITED": "no pre-demolition audit data; admitted at the default knockdown",
    "QUARANTINED_UNVERIFIED": "grade unverified; excluded unless --include-unverified",
    "QUARANTINED_CONDITION_D": "condition D (unsuitable); excluded from supply",
    "LOW_KNOCKDOWN": "derived/explicit knockdown below the floor; excluded from supply",
}

# Worst-severity -> Revit override colour (RGB 0-255), mirroring the writeback palette.
SEVERITY_COLOR = {
    "error": [214, 39, 40],   # red
    "warn": [255, 191, 0],    # amber
    "info": [160, 160, 160],  # grey
}


@dataclass
class MemberReview:
    id: str
    role: str
    raw_section: str
    section: str | None
    mapping_method: str
    condition: str
    verification: str
    knockdown: float
    defects: str
    recoverable_length_mm: float | None
    audited: bool
    admitted: bool
    has_coords: bool
    issues: list[list[str]]  # each entry is [code, severity]
    connection_type: str | None = None
    connection_condition: str | None = None
    deconstructability: str | None = None
    degree: int | None = None

    @property
    def worst_severity(self) -> str | None:
        sev = [s for _, s in self.issues]
        return max(sev, key=lambda s: SEVERITY_ORDER[s]) if sev else None

    def to_dict(self) -> dict:
        worst = self.worst_severity
        return {
            "id": self.id, "role": self.role, "raw_section": self.raw_section,
            "section": self.section, "mapping_method": self.mapping_method,
            "condition": self.condition, "verification": self.verification,
            "knockdown": self.knockdown, "defects": self.defects,
            "recoverable_length_mm": self.recoverable_length_mm,
            "audited": self.audited, "admitted": self.admitted, "has_coords": self.has_coords,
            "issues": [list(i) for i in self.issues],
            "connection_type": self.connection_type,
            "connection_condition": self.connection_condition,
            "deconstructability": self.deconstructability,
            "degree": self.degree,
            "worst_severity": worst,
            "color": SEVERITY_COLOR.get(worst),  # None when clean
        }


@dataclass
class ReviewModel:
    members: list[MemberReview]
    total: int = 0
    roles: dict[str, int] = field(default_factory=dict)
    mapped: int = 0
    fuzzy: int = 0
    unknown: int = 0
    with_coords: int = 0
    columns: int = 0
    columns_with_coords: int = 0
    audited: int = 0
    admitted: int = 0
    quarantined: int = 0
    avg_knockdown: float = 1.0
    verification_counts: dict[str, int] = field(default_factory=dict)
    condition_counts: dict[str, int] = field(default_factory=dict)
    issue_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "members": [m.to_dict() for m in self.members],
            "coverage": {
                "total": self.total, "roles": self.roles,
                "mapped": self.mapped, "fuzzy": self.fuzzy, "unknown": self.unknown,
                "with_coords": self.with_coords, "columns": self.columns,
                "columns_with_coords": self.columns_with_coords,
                "audited": self.audited, "admitted": self.admitted,
                "quarantined": self.quarantined, "avg_knockdown": self.avg_knockdown,
                "verification_counts": self.verification_counts,
                "condition_counts": self.condition_counts,
                "issue_counts": self.issue_counts,
            },
        }


def _mapping_method(member, catalog: dict, overrides: dict) -> str:
    """Reconstruct the per-member mapping method after resolve_members set ``member.section``.

    resolve_members reports counts by raw name, not by member; re-running map_section per member
    recovers the *name*-based method, and member.section (already set in place) tells us whether a
    fuzzy/unknown name was upgraded by measured-dimension geometry confirmation.
    """
    res = map_section(member.raw_section, catalog, overrides)
    if member.section and res.method in ("fuzzy", "unknown"):
        return "geometry"   # upgraded by measured dimensions
    if member.section is None and res.method == "fuzzy":
        return "fuzzy"      # quarantined near-miss
    if member.section is None:
        return "unknown"
    return res.method       # exact / override / normalized


def _member_issues(member, method: str, decision) -> list[list[str]]:
    issues = []
    if method == "unknown":
        issues.append(["UNKNOWN_SECTION", ISSUE_SEVERITY["UNKNOWN_SECTION"]])
    elif method == "fuzzy":
        issues.append(["FUZZY_MATCH", ISSUE_SEVERITY["FUZZY_MATCH"]])
    if not (getattr(member, "material_grade", None) or "").strip():
        issues.append(["MISSING_GRADE", ISSUE_SEVERITY["MISSING_GRADE"]])
    if not (member.start_xyz and member.end_xyz):
        issues.append(["NO_COORDS", ISSUE_SEVERITY["NO_COORDS"]])
    if not decision.audited:
        issues.append(["NOT_AUDITED", ISSUE_SEVERITY["NOT_AUDITED"]])
    elif not decision.admitted:
        if decision.condition.upper() == "D":
            issues.append(["QUARANTINED_CONDITION_D", ISSUE_SEVERITY["QUARANTINED_CONDITION_D"]])
        elif decision.reason_code == "below_floor":
            issues.append(["LOW_KNOCKDOWN", ISSUE_SEVERITY["LOW_KNOCKDOWN"]])
        else:
            issues.append(["QUARANTINED_UNVERIFIED", ISSUE_SEVERITY["QUARANTINED_UNVERIFIED"]])
    return issues


def extraction_review(model, catalog: dict, pda: str | None = None,
                      default_knockdown: float = 1.0, include_unverified: bool = False,
                      overrides: dict | None = None) -> ReviewModel:
    """Per-member data-quality + audit diagnostics for an extracted model.

    Reuses resolve_members (sets member.section in place, gives mapped/fuzzy/unknown counts) and
    assess_supply (knockdown/quarantine + coverage aggregates). ``pda`` is an optional audit CSV
    merged onto the members first, exactly as a match run does.
    """
    members = model.members
    if pda:
        from .core.audit import apply_audit, load_audit_csv
        apply_audit(members, load_audit_csv(pda))

    overrides = overrides or {}
    vr = resolve_members(members, catalog, overrides)   # sets m.section in place
    audit = assess_supply(members, default_knockdown, include_unverified)
    from .core.deconstruction import member_degrees
    degrees = member_degrees(model)

    reviews = []
    issue_counts = {}
    for m in members:
        method = _mapping_method(m, catalog, overrides)
        d = audit.decisions[m.id]
        issues = _member_issues(m, method, d)
        for code, _ in issues:
            issue_counts[code] = issue_counts.get(code, 0) + 1
        reviews.append(MemberReview(
            id=m.id, role=m.role, raw_section=m.raw_section, section=m.section,
            mapping_method=method, condition=d.condition, verification=d.verification,
            knockdown=round(d.knockdown, 3), defects=getattr(m, "defects", "") or "",
            recoverable_length_mm=getattr(m, "recoverable_length_mm", None),
            audited=d.audited, admitted=d.admitted,
            has_coords=bool(m.start_xyz and m.end_xyz), issues=issues,
            connection_type=getattr(m, "connection_type", None),
            connection_condition=getattr(m, "connection_condition", None),
            deconstructability=getattr(m, "deconstructability", None),
            degree=degrees.get(m.id),
        ))

    roles = {}
    for m in members:
        roles[m.role] = roles.get(m.role, 0) + 1
    cols = [m for m in members if m.role == "column"]
    return ReviewModel(
        members=reviews, total=len(members), roles=roles,
        mapped=len(vr.mapped), fuzzy=len(vr.fuzzy), unknown=len(vr.unknown),
        with_coords=sum(1 for m in members if m.start_xyz and m.end_xyz),
        columns=len(cols), columns_with_coords=sum(1 for m in cols if m.start_xyz),
        audited=audit.n_audited, admitted=audit.n_admitted, quarantined=audit.n_quarantined,
        avg_knockdown=audit.avg_knockdown, verification_counts=dict(audit.verification_counts),
        condition_counts=dict(audit.condition_counts), issue_counts=issue_counts,
    )
