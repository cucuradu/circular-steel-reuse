# Future improvements & known limitations

A living backlog from the 2026-06-02 code audit. **Tier 1 (correctness & honesty) is done** — see the
commit that adds this file. What follows is everything deferred, with severity, where it lives in the
code, why it matters, and a concrete fix sketch. Roughly priority-ordered within each tier.

Severity: 🔴 blocks credibility / wrong result · 🟠 important methodology gap · 🟡 minor / cosmetic.

---

## Tier 2 — Structural credibility (the thesis core)

### 🟡 1. Column loads — per-column tributary/floors (MOSTLY DONE), residuals below
**Shipped:** `estimate_column_loads` ([core/loads.py](src/steelreuse/core/loads.py)) derives a
per-column tributary **area** (2-D plan-grid, half-bay each side, edge = half present bay assuming no
overhang) and **floor count** (stack accumulation: the lowest column in a vertical stack carries every
floor above it). Wired through `AreaLoadModel`/`run_pipeline` under `--trib-from-geometry`, plus an
opt-in notional column **moment** via `--col-ecc` (eccentricity → `My_Ed = N·e`) so the N+M check
engages. The pyRevit extractor now also captures **point-placed column plan coordinates** (it only
recorded location-*curve* endpoints before, so every column's x,y was lost → the estimator could never
fire on real models). Verified end-to-end: lower-storey columns now carry N× the top, corner/edge/
interior tributaries differ (9/18/36 m² on a 6 m grid).

**Residuals (still open):**
- **Re-extract** the test models in real Revit so the columns carry coordinates (the bundled
  `pyrevit_extension/*_test2.json` were extracted with the old extractor → still no column x,y, so they
  fall back to the uniform default until re-run). This is the Tier-4 human task.
- **IFC extractor** ([ifc_extract.py](src/steelreuse/ifc_extract.py)) writes no `start_xyz`/`end_xyz`
  at all, so the IFC path can't use geometry loads either — add placement-transform extraction.
- **Frame moments** are still not modelled: `--col-ecc` is only a notional lever, not real beam-to-
  column moment transfer / unbalanced-span / sway moments.
- **Default-on?** Geometry estimation stays opt-in (`--trib-from-geometry`); flipping it on by default
  is a one-line change if desired (it already falls back per-member where geometry is missing).
- **Overhang**: the half-bay edge rule assumes the slab edge sits at the perimeter columns (no
  cantilever); a real overhang would add load.

### 🟠 2. Demand forces are *assumed*, not analyzed
The feasibility gate checks reclaimed members against a synthesized single ULS gravity case on simply-
supported spans — no lateral system, no load combinations, no pattern/notional/wind/seismic. Correct
for *pre-feasibility*, but it is **the** headline limitation and must be stated as such.

Fix sketch: add a small **load-combination envelope** (e.g. 1.35G+1.5Q, 1.0G+1.5Q, and a notional
horizontal) and take the worst per slot; document the gravity-only scope prominently in the report.

### ✅ 3. Avoided-new baseline leaks across standards (EU↔US) — DONE
`SectionProps` now carries a `standard` ("EU"/"US"); `baseline_new_mass_kg`
([match/optimize.py](src/steelreuse/match/optimize.py)) restricts the lightest-adequate search to the
slot's own standard (from its mapped design section, else its grade prefix), falling back to the whole
catalog only when the standard can't be determined. Reclaimed **supply** is intentionally left
unrestricted (cross-standard reuse is fine). Tested with two identical-geometry sections differing only
in mass + standard. *(Residual: a `--mixed-standards` opt-in if anyone ever wants the old behaviour.)*

### 🟠 4. EU catalog is thin — PARTIALLY DONE (validator added; HE/HEM still pending)
**Done:** added IPE550/IPE600 (verified against the eurocodeapplied table, which reproduces our IPE300
anchor exactly; `Iy` reconstructed from `Wel·h/2` to avoid the source's large-number display
truncation). Added a **catalog property-consistency test** (`test_catalog_property_consistency` in
[tests/test_sections.py](tests/test_sections.py)) that recomputes mass/`Wel`/`i` from the primaries and
checks `Wpl ≥ Wel` for **all 305 rows** (EU + the 283 US) — a transcription guard for any future row.

**Still pending:** [eu_sections.csv](src/steelreuse/data/sections/eu_sections.csv) is still light on
columns — no HEM at all, missing HEB220/260/280/320, several HEA, no UB/UC/UPN/IPN/angles. The blocker
is sourcing authoritative **plastic moduli** `Wpl`: the static-fetchable tables (piping-world,
build-your-vision) carry dimensions + `Wel` only, and the complete ones (Dlubal, eurocodeapplied HE
tabs) are JS-driven so a page fetch can't read them. *Do it properly:* obtain a machine-readable EU
dataset (EN 10365 / ArcelorMittal / a vetted CSV) and convert it verbatim like the AISC catalog was, so
`Wpl` is auditable rather than guessed; the consistency test will then guard the import. **Do not
hand-enter `Wpl` from memory** — it feeds bending capacity directly.

### 🟠 5. The flagship LTB (real χ_LT) is dormant in the default run
`AreaLoadModel` defaults `flange_restrained=True` (slab restrains the compression flange) ⇒ LTB is
skipped for **every** beam by default ([core/ec3_checks.py](src/steelreuse/core/ec3_checks.py)); the
real-χ_LT computation only fires when a user opts into unrestrained. Physically defensible, but the
showcase feature isn't exercised by the default path.

Fix sketch: report χ_LT alongside the restrained result anyway (informational), and/or add a
construction/erection-stage check where the slab isn't yet present so LTB governs.

### 🟡 6. Heavy-section edge cases (t_f > 40 mm)
Flexural-buckling curve selection (`_buckling_alpha`) and the `FY_BY_GRADE` table both assume
t_f ≤ 40 mm. Very heavy W-shapes in the AISC catalog have t_f > 40 mm → slightly non-conservative
curves and an overstated f_y.

Fix sketch: add the t > 40 mm buckling-curve shift and the reduced nominal f_y bands (EN 1993-1-1
Table 3.1 / 6.2), keyed off `sec.tf`; or flag/exclude those rows with a warning.

---

## Tier 3 — Narrative & validation

### 🟠 7. The "AI" is built but unwired (D1/D2/D3)
The surrogate, reuse-score, and clustering ([ml/](src/steelreuse/ml/)) are tested but the pipeline
([pipeline.py](src/steelreuse/pipeline.py)) never calls them — the matching flow is purely
deterministic. Also, the surrogate's R² ≈ 1.0 is **circular**: it is trained on labels produced by the
very EN checker it "predicts," on synthetic data.

Decision needed — pick one:
- **Wire it in**: reuse-score → a term in the MILP objective (prefer standardized/long stock);
  surrogate → a cheap feasibility pre-screen before the exact check, and report the measured speedup.
- **Reframe**: present the ML as an exploratory side-study in the writeup and drop the strong-accuracy
  framing (state the circularity honestly).

### 🟠 8. No methodology document (roadmap WS3)
A thesis needs a methods section mapping **each EN 1993-1-1 clause → code → assumption → validation
source**. Doesn't exist yet.

Fix sketch: `docs/METHODOLOGY.md` with a clause table (classification 5.2, 6.2.x resistances, 6.3.x
buckling/LTB, 6.3.3 interaction, SLS), every assumption (γ factors, C1=1.0, k=1.0, knockdown,
restraint, carbon factors), and an **end-to-end validation against one worked textbook example**.

### 🟡 9. Optimizer / reporting refinements
- **Off-cut as pure waste**: the objective penalizes off-cut but a long donor cut to a short slot
  leaves reusable remainder; the cutting-stock extension (1 donor → many cuts) would model this and
  remove the bias against long stock. (Was Tier 1's deliberate "soft preference" choice; cutting-stock
  is the real fix.)
- **N+M interaction** is a simplified linear sum (no 6.3.3 k-factors). Conservative for k ≤ 1 / C1 = 1,
  but label it clearly or implement the full 6.3.3 form.
- **No shear–moment (6.2.8) interaction** and **no biaxial bending (Mz)** — fine for gravity UDL
  (M and V peaks are at different points) but document.
- **Single k_y = k_z = 1.0** effective length for all columns — pinned-pinned assumption; expose per
  member or infer from end fixity.

---

## Tier 4 — Human-only (cannot be automated)

### 10. Run the pyRevit extractor on a real steel model
The extractor ([extractor/pyrevit_extract.py](extractor/pyrevit_extract.py)) has never been run in
real Revit (Phase 1's official check). Build a small donor + demand model, run the SteelReuse → Extract
button, and confirm the JSON member count matches a Revit structural schedule. See
[TODO.md](TODO.md) §4.

### 11. Material-reuse verification model (currently disclaimer-only)
Coupon testing, corrosion/fatigue survey, weldability of old steel, and **connection design** are the
real-world determinants of reuse feasibility and are explicitly out of scope — only a disclaimer
covers them. A future version could at least ingest coupon-test results to set the per-member
`knockdown` instead of a global default.

---

## Done in Tier 1 (for reference)
- 🔴 Catalog/carbon CSVs moved into the package (`src/steelreuse/data/`) so an installed wheel finds
  them (was `parents[3]`, outside the wheel). Verified the wheel now bundles them.
- 🟠 Greedy fallback no longer books net-negative (CO₂-losing) matches — mirrors the MILP.
- 🟠 Booked/reported "CO₂ saved" is now the net figure the optimiser uses (includes connection
  refabrication carbon); off-cut is an explicit soft preference, not booked.
- 🟡 README/CLAUDE install command fixed (was missing the `report`/`bim` extras → Jinja2 absent →
  CLI crash at HTML render); stale paths and test counts refreshed; removed dead `STEEL_DENSITY_KG_M3`.
