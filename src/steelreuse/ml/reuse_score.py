"""Reuse-potential score (0..1) — a *non-formula* judgment, the honest job for ML here.

There is no closed form for "how reusable is this member", so we score it from observable proxies:
  * standardization — how often the same section repeats in the stock (repeated = easy to reuse);
  * length usability — longer members are more reusable (they can be cut down to fit).

It is implemented as a transparent weighted heuristic so it is explainable today; a trained model
can replace it once real reuse outcomes are available, behind the same function signature.
"""

from __future__ import annotations

from .clustering import group_by_section


def reuse_scores(
    members,
    length_ref_mm: float = 8000.0,
    w_standardization: float = 0.6,
    w_length: float = 0.4,
) -> dict[str, float]:
    """Return ``{member_id: score in [0, 1]}``. Unmapped members score 0 (cannot be reused as-is)."""
    groups = group_by_section(members)
    max_count = max((len(v) for v in groups.values()), default=1)
    scores: dict[str, float] = {}
    for m in members:
        if not m.section:
            scores[m.id] = 0.0
            continue
        standardization = len(groups[m.section]) / max_count
        length_use = min((m.length_mm or 0.0) / length_ref_mm, 1.0)
        scores[m.id] = round(w_standardization * standardization + w_length * length_use, 4)
    return scores
