"""Donor-row **mismatch log** (Roadmap §1.2) — every donor member classified, with a reason.

Where :func:`steelreuse.match.optimize.diagnose_match` explains the *demand* side (why slots went
unfilled), this is its *donor* companion: it accounts for **100 % of donor rows**, classifying each as

  * ``mapped``      — name resolved to a catalogue section AND admitted to the certified supply;
  * ``fuzzy``       — a near-miss name match, quarantined pending an override (never silently used);
  * ``unknown``     — no catalogue match and no geometry confirmation (excluded);
  * ``quarantined`` — name mapped, but the pre-demolition audit excluded it (unverified / unsuitable /
                      knockdown below the floor),

each with a human-readable reason, so nothing is ever silently dropped. The log is surfaced in the
evidence package (:mod:`steelreuse.evidence`) and is built from the section :class:`ValidationReport`
(by member id) and the :class:`AuditSummary`, cross-referenced with the match result for the outcome
(reused / unused) of admitted donors.

Standard library only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

_METHOD_PHRASE = {
    "exact": "exact name match",
    "normalized": "normalized name match",
    "override": "user override",
    "geometry": "identified by measured dimensions",
}


@dataclass
class MismatchRow:
    """One donor member's provenance verdict (see :func:`build_mismatch_log`)."""

    id: str
    raw_section: str
    canonical: str | None
    method: str               # exact | override | normalized | geometry | fuzzy | unknown
    classification: str       # mapped | fuzzy | unknown | quarantined
    reason: str
    outcome: str = ""         # reused | unused | "" (only meaningful for admitted/mapped donors)
    knockdown: float | None = None


def build_mismatch_log(donor_members, validation, audit=None, result=None) -> list[dict]:
    """Classify every donor member; return one dict per row (keys = :class:`MismatchRow` fields).

    ``validation`` is the donor :class:`~steelreuse.core.sections.ValidationReport` (its
    ``by_member_id`` carries the final mapping result, including a geometry upgrade); ``audit`` is the
    :class:`~steelreuse.core.audit.AuditSummary`; ``result`` (optional) is the
    :class:`~steelreuse.match.optimize.MatchResult`, used only to mark admitted donors reused/unused.

    Mapping failure dominates the verdict: an unmapped (unknown) or quarantined-fuzzy name is reported
    as such even if it also carries audit data, because an unmapped member never reaches the supply the
    audit gates. A mapped name is then admitted or quarantined by the audit.
    """
    by_id = getattr(validation, "by_member_id", {}) or {}
    decisions = getattr(audit, "decisions", {}) if audit is not None else {}
    reused = {a.supply_id for a in result.assignments} if result is not None else set()

    rows: list[MismatchRow] = []
    for m in donor_members:
        mid = getattr(m, "id", "?")
        raw = getattr(m, "raw_section", "") or ""
        mr = by_id.get(mid)
        method = mr.method if mr is not None else "unknown"
        canonical = mr.canonical if mr is not None else getattr(m, "section", None)
        decision = decisions.get(mid)
        knockdown: float | None = None
        outcome = ""

        if method == "unknown":
            classification = "unknown"
            reason = f"raw name {raw!r} matched no catalogue section (and no geometry confirmation)"
        elif method == "fuzzy":
            classification = "fuzzy"
            cands = ", ".join(mr.candidates) if mr and mr.candidates else (canonical or "?")
            reason = (f"fuzzy name match to {canonical} (confidence {mr.confidence:.2f}; "
                      f"candidates: {cands}) — quarantined pending override confirmation")
        elif decision is not None and decision.audited and not decision.admitted:
            classification = "quarantined"
            reason = (f"mapped to {canonical} but quarantined by the pre-demolition audit: "
                      f"{decision.reason}")
        else:
            classification = "mapped"
            phrase = _METHOD_PHRASE.get(method, f"{method} match")
            reason = f"{phrase} to {canonical}"
            if decision is not None and decision.audited:
                knockdown = round(decision.knockdown, 3)
                reason += f" (audit f_y knockdown {knockdown:g})"
            outcome = "reused" if mid in reused else "unused"

        rows.append(MismatchRow(id=mid, raw_section=raw, canonical=canonical, method=method,
                                classification=classification, reason=reason,
                                outcome=outcome, knockdown=knockdown))
    return [asdict(r) for r in rows]


def mismatch_summary(rows: list[dict]) -> dict:
    """Counts per classification + a 100%-coverage flag, for the console line and evidence package."""
    n = len(rows)
    counts = {"mapped": 0, "fuzzy": 0, "unknown": 0, "quarantined": 0}
    reused = unused = 0
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
        if r.get("outcome") == "reused":
            reused += 1
        elif r.get("outcome") == "unused":
            unused += 1
    return {
        "n_donor_rows": n,
        **counts,
        "reused": reused,
        "unused": unused,
        # Every row carries exactly one of the four classifications, so the buckets must sum to n.
        "accounts_for_all": sum(counts.values()) == n,
    }
