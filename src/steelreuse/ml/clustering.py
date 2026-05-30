"""Group donor members into standardized families.

Two views:
  * :func:`group_by_section` — exact grouping (identical catalog section). Standardized stock that
    repeats is the easiest and most valuable to reuse, and shrinks the matching problem.
  * :func:`cluster_similar` — KMeans on size features to group *similar* (not identical) sections,
    e.g. when a model mixes near-equivalent profiles.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from ..core.sections import SectionProps


def group_by_section(members) -> dict[str, list]:
    """Map canonical section name -> list of members. Unmapped (section is None) are skipped."""
    groups: dict[str, list] = defaultdict(list)
    for m in members:
        if m.section:
            groups[m.section].append(m)
    return dict(groups)


def cluster_similar(
    members, catalog: dict[str, SectionProps], n_clusters: int = 3, random_state: int = 0
) -> dict[str, int]:
    """KMeans cluster mapped members by (h, b, A). Returns ``{member_id: cluster_index}``.

    ``n_clusters`` is clamped to the number of distinct mapped members so tiny models don't error.
    """
    mapped = [m for m in members if m.section and m.section in catalog]
    if not mapped:
        return {}
    feats = np.array([[catalog[m.section].h, catalog[m.section].b, catalog[m.section].A]
                      for m in mapped])
    k = max(1, min(n_clusters, len({m.section for m in mapped})))
    X = StandardScaler().fit_transform(feats)
    labels = KMeans(n_clusters=k, n_init=10, random_state=random_state).fit_predict(X)
    return {m.id: int(lbl) for m, lbl in zip(mapped, labels, strict=True)}
