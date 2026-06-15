"""ML layer — an **exploratory side-study**, deliberately NOT wired into the matching pipeline.

The certified result path is entirely deterministic (EN 1993 checks → carbon → MILP). These modules are
kept for exploration and a possible future role, not as authority:

* ``surrogate`` — an XGBoost regressor that imitates the deterministic utilization. Its high R² is
  **circular**: it is trained on labels produced by the EN 1993 checker itself over a synthetic sweep,
  so the score only shows it can reproduce the checker, *not* any real-world predictive power. It is
  never the source of truth and is not used to gate matches.
* ``reuse_score`` — a transparent weighted heuristic (standardization × length usability), explainable
  today and replaceable by a trained model once real reuse outcomes exist.
* ``clustering`` — KMeans grouping of similar sections, for exploration.

To actually use any of these in the pipeline, see docs/OVERVIEW.md §10 (a deliberate decision, not
a default).
"""
