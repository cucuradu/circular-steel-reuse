# Future improvements & known limitations

A living backlog from the 2026-06-02 code audit. **Tier 1 (correctness & honesty) is done** — see the
commit that adds this file. What follows is everything deferred, with severity, where it lives in the
code, why it matters, and a concrete fix sketch. Roughly priority-ordered within each tier.

Severity: 🔴 blocks credibility / wrong result · 🟠 important methodology gap · 🟡 minor / cosmetic.

---

## ✅ Global frame analysis (`--frame-analysis`, `core/frame.py`) — NEW

The headline methodology gap was that member forces were **synthesised in isolation** (every beam a
pin-roller `wL²/8` span, every column bare axial), with no connected structure. There is now an opt-in
**global frame solve** (PyNiteFEA): the demand model is assembled by snapping coincident endpoints into
shared nodes and solved as a **simple braced frame** (pinned beams, continuous columns, fixed bases), and
the resulting `MemberDemand` feeds the *same* EN 1993 check + matcher. Floor load goes on the beams; each
**column axial then comes from the real load path** (multi-storey accumulation; interior columns collect
from both sides) — this supersedes the tributary-area/floor-count estimate of Tier-2 #1. Validated against
the closed-form simply-supported result and hand statics (`tests/test_frame.py`); falls back to the
analytic path per member where geometry is missing.

**Lateral / sway — DONE (EN 5.3.2 EHF + P-Δ).** With `--phi`, the global sway imperfection is applied as
**equivalent horizontal forces** `H_i = φ·N_Ed` at each column top (from the gravity column axials), in
each lateral direction, and the frame is solved **2nd-order (P-Δ)**. The lateral load is carried by the
model's vertical bracing (pin-ended axial `brace` members) or, absent bracing, the fixed column bases; the
member force envelope spans gravity + the sway cases and the matcher reports the governing one. `--pdelta`
forces 2nd-order without a sway case. Replaces the member-level notional moment when frame analysis is on.

**Wind — DONE.** `--wind q` (kN/m², the user's EN 1991-1-4 net pressure) applies horizontal **storey
forces** `q·width_perp·h_trib` (building plan extent perpendicular to the wind × storey tributary height)
lumped onto each level's column tops, as a **wind-leading** combination `γ_G·G + γ_Q·W + γ_Q·ψ₀·Q`
(`ψ₀ = 0.7`) carrying the sway imperfection. Needs a 3-D model (planar frames have no façade → skipped with
a warning). `wind_node_forces` is unit-tested against hand arithmetic on a 3-D box.

**Multi-span members — DONE.** `expand_spans` ([core/frame.py](src/steelreuse/core/frame.py)) splits a
continuous beam (`spans_mm = [s₁, s₂,…]`) into one sub-element per span at its interior supports
(interpolated along the member axis so the interior nodes land on the columns below). Each bay is then
checked over its own length and its reaction routed into the correct interior column (previously the whole
load dumped at the two far ends, leaving interior columns unloaded); the pipeline makes one slot per span.

**Seismic — DONE (lateral force method).** `--seismic Cs` ([core/frame.py](src/steelreuse/core/frame.py)
`seismic_node_forces`) applies the EN 1998-1 §4.3.3.2 lateral force method: seismic weight per level
`W_i = Σ(g_k+ψ₂·q_k)·trib·L`, base shear `F_b = Cs·ΣW_i`, inverted-triangular distribution
`F_i = F_b·(W_i·z_i)/Σ(W_j·z_j)` lumped on the level's column tops, as a `G + ψ₂·Q + E` situation (unit
factors). `Cs = Sd(T₁)·λ/g` is a user input.

**Robustness on real BIM (added):** the assembler now supports each **disconnected component at its own
lowest level**, releases only the **major-axis** beam moment (kills spurious vertical-axis rotational
singularities while keeping `wL²/8`), **prunes members that hang off** the structure to the analytic
path, and **guards against ill-conditioned "successes"** (non-physical forces → fall back). On a real
~1000-member building (see `docs/CASE_STUDY.md`) the demand model turned out to be three disconnected
irregular pieces that form a near-mechanism; the tool correctly falls back rather than emit garbage.

**Residuals (the obvious next increments):**
- **Auto-idealisation of irregular multi-piece BIM:** turning an arbitrary, disconnected real model into
  a *well-conditioned* global frame (vs. the current "solve cleanly or fall back") is the open problem —
  e.g. per-piece solves, mechanism detection/repair, or user-guided support assignment.
- **Modal/response-spectrum seismic:** the current seismic is the simplified lateral force method with a
  user base-shear coefficient — no modal spectrum, accidental torsion, or `q`-factor/site spectrum.
- ✅ **Biaxial columns — DONE (full 6.3.3).** The per-combo envelope now carries `M_y` and `M_z`
  separately into `MemberDemand`, and the checker runs the **full EN 1993-1-1 6.3.3 interaction**
  (eq. 6.61/6.62, Annex B Method 2 factors, `C_m = 1.0` conservative) plus a minor-axis bending check —
  see METHODOLOGY §5.5. Residual: member rotation about its own axis isn't captured from the BIM, so
  the local→section axis mapping assumes the default orientation.
- **Effective lengths** still `k = 1.0` (the solve gives forces, not buckling lengths); a sway/non-sway
  classification from the frame is a future refinement.
- **IFC path** still writes no coordinates, so frame analysis only runs on the pyRevit/coordinate-bearing
  models (ties into Tier-2 #1's IFC residual below).

---

## ✅ Geometry confirmation of fuzzy/unknown section names — NEW

Fuzzy name matches were quarantined until a human confirmed them via the override CSV. The extractors
now capture each member's **measured section dimensions** (`h/b/tf/tw` — pyRevit structural-section
type parameters; IFC `IfcIShapeProfileDef`), and `resolve_members` confirms a fuzzy or unknown name
when those dimensions match **exactly one** catalog row within `max(1 mm, 1.5 %)` (method `geometry`,
confidence 1.0). A fuzzy name needs `h+b`; an unknown name needs all four dimensions. Ambiguity
confirms nothing; models without dimensions behave exactly as before. **Residual:** the bundled sample
models predate dimension capture (re-extract to benefit); a Streamlit review queue for the remaining
unconfirmed fuzzy matches would close the loop.

---

## ✅ Connection feasibility screen — NEW (geometry; design still out of scope)

Connections often govern whether reuse is practical, but the tool treated them as a flat 5 kg
refabrication penalty. `core/connections.py` now screens each (donor, slot) pair **geometrically
against the slot's design section** (what the connections were detailed around): wrong shape family or
> 50 mm deeper → `incompatible`; markedly shallower / thinner web (bolt bearing) / narrower flange
(seats, end plates) → `review`. Every assignment is annotated (report "Connection" column +
`Assignment.connection_status/note`); `--connections` (CLI), `connection_screen=` (pipeline), or the
app checkbox additionally **exclude incompatible pairs** before matching. No design section → no
opinion (never blocks). Tolerances live in `ConnectionPolicy`.

**✅ Capacity extension — DONE.** `standard_shear_capacity` gives each open-section donor a
lower-bound *standard fin-plate* shear resistance (single row of M20 8.8 bolts in a 10 mm S275 plate,
row count from the clear web depth, per-bolt EN 1993-1-8 Table 3.4 minimum of bolt shear and bearing
with conservative `α_b = 0.5`; IPE300 → 3 rows ≈ 183 kN, hand-verified in `tests/test_connections.py`).
The matcher passes the slot's worst `V_Ed` into the screen; exceeding the standard capacity flags
`review` ("bespoke end connection required") — never a gate, because a bespoke connection may work.
**Residual:** actual connection design (welds, block tearing, end plates in tension, moment
connections) remains the engineer's; tube end connections get no capacity opinion.

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

### ✅ 2. Demand forces — load-combination envelope (DONE; residual: wind/seismic + real frame analysis)
**Was:** the feasibility gate checked each member against a *single* synthesized ULS gravity case, so
"no load combinations" was the headline limitation.

**Done:** members are now verified against an explicit **load-combination envelope**
(`AreaLoadModel.combination_loads` → `pipeline.build_slots` → `match._feasible_cell`). Each `DemandSlot`
carries a list of `(name, MemberDemand)`; the matcher checks **every** combination, reports the
**governing** (worst-utilisation) one (`Assignment.governing_combination`, shown as a "Gov. load case"
report column), and a reuse — *and* the avoided-new baseline (`_passes_all`) — passes only if it passes
all of them. The default envelope is the gravity case (`γ_G g_k + γ_Q q_k`, EN 6.10) plus, for columns,
an opt-in **EN 1993-1-1 §5.3.2 global sway imperfection** (`--phi`, e.g. `0.005 = 1/200`) applied as a
notional column moment `M_y,Ed = N_Ed·φ·L`, which engages the N+M interaction. `φ = 0` (default) ⇒
gravity only, so default results are byte-identical; with realistic multi-floor columns, `--phi 0.005`
demonstrably flips the governing case and can force a heavier section/baseline. Documented in
[METHODOLOGY §4/§7/§9/§12](docs/METHODOLOGY.md).

**Residual (still open):** the envelope ships only gravity + the notional-sway case — **wind, seismic,
pattern and uplift (`1.0G+1.5Q`) combinations** are not yet populated (they plug in as extra entries),
and the sway case is a **member-level** notional moment, not a real **frame analysis** (no sway/2nd-order
solve, no lateral system). Flipping `--phi` on by default is a one-line change if wanted.

### ✅ 3. Avoided-new baseline leaks across standards (EU↔US) — DONE
`SectionProps` now carries a `standard` ("EU"/"US"); `baseline_new_mass_kg`
([match/optimize.py](src/steelreuse/match/optimize.py)) restricts the lightest-adequate search to the
slot's own standard (from its mapped design section, else its grade prefix), falling back to the whole
catalog only when the standard can't be determined. Reclaimed **supply** is intentionally left
unrestricted (cross-standard reuse is fine). Tested with two identical-geometry sections differing only
in mass + standard. *(Residual: a `--mixed-standards` opt-in if anyone ever wants the old behaviour.)*

### ✅ 4. EU catalog expanded — DONE for the common range (residual: small sizes + other families)
**Done:** [eu_sections.csv](src/steelreuse/data/sections/eu_sections.csv) now covers IPE160–600 and the
**full HEA / HEB / HEM 200–400 column range** (40 EU sections, up from 20). Sourced from the ArcelorMittal
section table in the [PedroBiel gist](https://gist.github.com/PedroBiel/980d8bc914d8abffca6d)
(`sections_ArcelorMittal.csv`) — validated by checking that the source reproduces our hand-verified
IPE300 / HEB200 / HEB240 / HEB300 rows (incl. `Wpl`) **exactly** before trusting its `Wpl` column for the
new sizes. Every added row passes the **catalog property-consistency test**
(`test_catalog_property_consistency` in [tests/test_sections.py](tests/test_sections.py)), which
recomputes mass/`Wel`/`i` from the primaries and checks `Wpl ≥ Wel` across all rows (now 711: 40 EU +
283 US W + 388 US HSS).

**✅ HSS — DONE (shape-aware).** The **388 rectangular/square AISC HSS** now ship in
[us_hss.csv](src/steelreuse/data/sections/us_hss.csv) (verbatim imperial from the AISC v15.0 database,
design wall `t_des = 0.93·t_nom`) with **hollow-section rules** in the checker: every wall classified as
an internal part (`c = h − 3t`, Table 5.2), cold-formed **buckling curve c** both axes, RHS shear area
`A_v = A·h/(b+h)`, and **no LTB** (closed sections aren't susceptible; the open-section `I_t`/`I_w`
approximations don't apply). The avoided-new baseline is additionally **shape-family-restricted**: a
tube baseline only when the design section is a tube, so existing open-section results are unchanged.
Tested in [tests/test_hss.py](tests/test_hss.py).

**Residual (lower priority):** small sections (HEA/HEB/HEM 100–180, IPE80–140) and the remaining
families (UB/UC, UPN/UPE channels, IPN, L-angles, round CHS/pipe) — the EU gist covers most of the open
ones; round tube needs a `D/t` classification rule, and channels/angles need mono-symmetric checks
(shear-centre offset, eccentric connections), which is why they stay excluded.

### ✅ 5. Surface χ_LT in the default run — DONE
The restrained bending path now also computes the **"if unrestrained" χ_LT**
([core/ec3_checks.py](src/steelreuse/core/ec3_checks.py)) and warns when it would fall below 0.85, so the
flagship LTB calculation is visible even though a slab-restrained beam correctly uses χ_LT = 1.0. The
matcher threads `chi_lt` / `chi_lt_if_free` onto each `Assignment`; the report shows a **χ_LT column** and
a note counting beams that pass only because of the slab restraint (construction-stage risk). On the
sample model, 4 reused beams are flagged (χ_LT 0.45–0.71 if unrestrained).

✅ **Construction-stage load case — DONE.** `--construction` (CLI) / `construction_stage=True`
(`AreaLoadModel`) appends a **bare-steel erection-stage entry** to every beam slot's envelope:
full permanent (wet slab) + the EN 1991-1-6 construction live load (`--construction-live`,
default 0.75 kN/m²) with the compression flange **unrestrained**, so `chi_LT` applies in earnest.
Works on both the analytic and frame paths (the stage deliberately uses isolated-span statics — the
diaphragm the frame assumes is not yet present). A reuse and the avoided-new baseline must pass the
stage like any other combination; the report shows it as the governing case where it bites. Tested
end-to-end in `tests/test_match.py`: an IPE300 donor passes gravity restrained but is **rejected** for
a 6 m IPE300 slot under the stage (`chi_LT(6 m) ≈ 0.45` → `M_b,Rd ≈ 77.7 < 78.975 kNm`).

### ✅ 6. Heavy-section edge cases (t_f > 40 mm) — DONE
**Was:** `_buckling_alpha` and the `FY_BY_GRADE` table both assumed `t_f ≤ 40 mm`, so the 88 AISC
W-shapes with `t_f > 40 mm` got slightly non-conservative buckling curves and (for EN grades) an
overstated `f_y`.

**Done:**
- `_buckling_alpha` ([core/ec3_checks.py](src/steelreuse/core/ec3_checks.py)) now selects the EN 1993-1-1
  **Table 6.2** curve from `h/b` *and* `t_f`: `40 < t_f ≤ 100 mm` shifts y→b / z→c, and `t_f > 100 mm`
  → curve d both axes.
- `nominal_fy(grade, t_f)` ([core/sections.py](src/steelreuse/core/sections.py)) applies the **Table 3.1**
  thickness bands for EN 10025 grades (e.g. S355 → 335 N/mm² for `40 < t ≤ 80 mm`); ASTM grades carry a
  single specified minimum `F_y` and are unchanged. `check_member` uses it (keyed off the flange `t_f`)
  and flags heavy sections + the `f_y` reduction in the member warnings.
- Tested in [tests/test_ec3.py](tests/test_ec3.py) (bands, curve shift, lower χ, EN-vs-ASTM warning).

---

## Tier 3 — Narrative & validation

### ✅ 7. The "AI" story — DECIDED: exploratory, reframed honestly
**Decision (user):** keep the ML as an **exploratory side-study**, not wired into the certified path.
Reframed accordingly: [ml/__init__.py](src/steelreuse/ml/__init__.py) and
[ml/surrogate.py](src/steelreuse/ml/surrogate.py) now state the surrogate's R² ≈ 1.0 is **circular**
(trained on labels from the EN checker itself → only shows it can reproduce the checker), the README
status row marks Phase 4 exploratory, and [METHODOLOGY §11](docs/METHODOLOGY.md) documents all three
modules as not-in-the-pipeline.

**If ever wired in (future, deliberate):** reuse-score → a term in the MILP objective (prefer
standardized/long stock); surrogate → a cheap feasibility pre-screen before the exact check, reporting
the measured speed-up. Would need a non-circular validation (real reuse outcomes, or hold-out cases the
checker and surrogate can disagree on).

### ✅ 8. Methodology document — DONE
[docs/METHODOLOGY.md](docs/METHODOLOGY.md) maps each EN 1993-1-1 clause → code → assumption → validation
source (classification 5.2, 6.2.x resistances, 6.3.x buckling/LTB, the full 6.3.3 interaction,
SLS), with an assumptions register and the hand-calc validation basis. **The end-to-end worked example
is now DONE** ([tests/test_worked_example.py](tests/test_worked_example.py) + the "Worked example"
section of [docs/VALIDATION.md](docs/VALIDATION.md)): one complete bay through `run_pipeline` with every
stage — pressure, w, M/V/N, resistances, χ, baselines, carbon — asserted against the hand chain.
**Residual:** the example is self-derived; a cross-check against an independently *published* design
example (e.g. an SCI/Access-Steel worked beam+column) would add external authority.

### 9. Optimizer / reporting refinements
- ✅ **Off-cut / cutting-stock — DONE (optional mode).** `match(..., allow_cutting=True)` / CLI `--cut`
  ([match/optimize.py](src/steelreuse/match/optimize.py)) lets one donor be cut into several pieces for
  several slots, bounded by `Σ(required_len + cut tolerance) ≤ donor length` (both the MILP and the
  greedy fallback respect the cap). The off-cut penalty is dropped in this mode (the remainder is
  genuinely reusable — the real fix for the long-stock bias), and each cut donor's leftover is reported
  as reusable remainder (`MatchResult.donor_leftover_mm`, surfaced in the report + CLI). The default
  stays one-piece-per-donor (conservative). Tested in [tests/test_match.py](tests/test_match.py).
- ✅ **N+M interaction — DONE (full 6.3.3).** The simplified linear sum is replaced by eq. (6.61)/(6.62)
  with **Annex B (Method 2)** k-factors (Tables B.1/B.2, susceptible/not-susceptible `k_zy`, RHS `k_zz`
  variant, all `C_m = 1.0` → conservative for any moment shape), **biaxial** (`M_z,Ed` is a first-class
  demand), LTB-aware exactly as the code equations prescribe. Hand-validated in `tests/test_ec3.py`
  (IPE300 chain in the test comments). Minor-axis-only bending and the no-axial biaxial cross-section
  sum are separate checks.
- ✅ **Shear–moment (6.2.8) interaction — DONE.** Above `0.5·V_pl,Rd` the bending check now uses the
  ρ-reduced resistance (eq. 6.30 for rolled I/H, `(1−ρ)·M_c,Rd` for tubes), with peak `M` and `V`
  treated as coincident (conservative for a UDL span). Hand-verified IPE300 chain in `tests/test_ec3.py`.
- **Single k_y = k_z = 1.0** effective length for all columns — pinned-pinned assumption; expose per
  member or infer from end fixity.

---

## Tier 4 — Human-only (cannot be automated)

### 🟡 10. pyRevit extractor on a real steel model — RUN, two residuals
The extractor ([extractor/pyrevit_extract.py](extractor/pyrevit_extract.py)) **has now been run in real
Revit** (2026-06-09): the case-study building was re-extracted to `pyrevit_extension/donortest3.json` /
`demandtest3.json` with full column coordinates (74/74 donor, 54/54 demand) and the pipeline reproduces
the test2 headline (140/349 reused, 16.5 t CO₂). **Residuals:** (a) the formal completeness check —
member count vs a Revit structural schedule via `steelreuse-validate --schedule` — hasn't been ticked;
(b) the test3 extraction predates the measured-dimension capture, so one more re-extraction is needed
for geometry auto-confirmation of fuzzy names to engage on the real model. See [TODO.md](TODO.md).

### ✅ 11. Material-reuse verification model — DONE (pre-demolition audit layer)
**Was:** coupon testing / corrosion survey / grade traceability were disclaimer-only, and the reclaimed
`knockdown` was a single global value applied to all donor stock.

**Done:** a **pre-demolition audit** layer ([core/audit.py](src/steelreuse/core/audit.py)) ingests, per
donor member, a surveyed **condition grade** (A–D) and **verification basis** (mill cert / coupon test /
documented / visual / unverified) — set in the model JSON or merged from a CSV via `--pda`. It derives a
**per-member f_y knockdown** (condition × verification factor, or an explicit auditor value) and
**quarantines** unverified or unsuitable (condition D) stock from the certified supply, exactly like a
fuzzy section match — honest by default (a member with no audit data is unchanged; absence ≠ "fine").
`recoverable_length_mm` feeds the matcher's length/cutting constraints. Provenance surfaces in the
material passport, the HTML report (a Provenance column + an audit summary), and the console. See
[docs/PRE_DEMOLITION_AUDIT.md](docs/PRE_DEMOLITION_AUDIT.md) and `tests/test_audit.py`.

**Residual:** still out of scope (engineer's responsibility) — designing the coupon-test programme,
weldability of old steel, and **connection design / condition**; the layer models how audit *findings*
flow into the result, not the survey itself. The condition→knockdown factors are representative defaults
(documented in METHODOLOGY §3.1), not a code-calibrated derating model.

---

## Ideas backlog — 2026-06-10 brainstorm (all fronts)

A curated, all-sides idea sweep recorded before the next re-extraction. ★ marks the ten items judged
best value-for-effort. Nothing here is committed work; promote an item by moving it into a Tier above
with an owner and a fix sketch.

### I-1. Structural checks (depth)
- ★ **Effective length from the frame.** Compute `α_cr` (amplified-sway ratio or a PyNite buckling
  solve) → non-sway classification per EN 1993-1-1 5.2.1(3) (`α_cr ≥ 10`), auto-decide when 2nd-order
  is mandatory, and support a per-member `k` override (JSON field + CSV) instead of blanket `k = 1.0`.
- **C_m from the moment shape.** The frame solve knows each member's end-moment ratio ψ → Annex B
  Table B.3 `C_m` instead of the conservative 1.0; the same data yields **C₁** for `M_cr` (sharper LTB).
- **Class 4 effective-section properties** (EN 1993-1-5 §4) so slender sections get a number, not
  just REVIEW.
- **Net-section at existing bolt holes** (`0.9·A_net·f_u/γ_M2`): reclaimed members arrive with holes.
  Add a per-end hole register to the PDA schema; deduct in tension/bending. Genuinely reuse-specific.
- **Corrosion section loss**: PDA field "measured thickness loss (%)" → scale `t/A/W/I` per member
  before checking (today condition only knocks down f_y, which is not the right physics for section loss).
- **Web bearing/buckling at new support points** (EN 1993-1-5 §6): a reused beam is re-supported at
  new locations; flag where stiffeners would be needed.
- **Floor-vibration screen**: `f₁ ≈ 18/√δ_perm`; flag floors below ~4 Hz. Cheap and very professional.

### I-2. Actions & combinations
- ★ **Load reversal / wind-uplift case** (`1.0·G + 1.5·W`): hogging puts the **bottom** flange in
  compression where no slab restrains it — the one remaining non-conservative blind spot of the
  restrained-flange default. Needs the wind path + a reversed-restraint LTB check.
- **Pattern live load** for continuous beams in the frame path (alternate-span DL/LL cases).
- **ψ-factors by occupancy category** (EN 1990 Table A1.1) instead of the fixed 0.7/0.3.
- **Snow** (EN 1991-1-3) for roof members (detectable: highest-level beams).
- **Flip `φ = 1/200` ON by default** — imperfections always exist; one line, honest default
  (documented byte-identical escape hatch: `--phi 0`).

### I-3. Frame analysis
- ★ **Member rotation capture** (Revit *Cross-Section Rotation* parameter) → correct local→section
  axis mapping, closing the biaxial orientation residual (METHODOLOGY §9 register row).
- **Modal analysis → T₁ → EN 1998 design spectrum → derive Cs** in-tool (today Cs is a user input).
- **Moment-frame / semi-rigid option**: capture per-end releases from Revit instead of assuming all
  beams pinned; opens portal-frame donors/demands.
- **Rigid-diaphragm constraint** per floor (better lateral force distribution than equal lumping).
- **Mechanism auto-repair** for irregular multi-piece BIM (existing residual): per-piece solves,
  detection of under-constrained pieces, user-guided support assignment.

### I-4. Matching & optimisation
- ★ **Multi-objective**: CO₂ + cost (€/t reclaimed vs new + refabrication) + transport. Weighted-sum
  in the existing MILP first; pymoo NSGA-II for a Pareto front later (optional extra exists).
- ★ **A4 transport emissions**: donor-site and new-site locations → t·km × mode factor, added to the
  passport and the optimiser's net figure. The most-asked LCA question.
- **Donor splicing** (two donors → one long slot; the converse of cutting-stock, with a splice
  penalty + connection screen on the joint).
- **Same-section grouping preference**: soft objective term rewarding repeated sections across slots
  (constructability / fewer connection types).
- **Residual-stock export**: unmatched donors → a CSV directly loadable as the *next* project's
  supply — circularity chaining, a strong thesis narrative.
- **Match robustness badge**: rerun the match at ±10 % loads; per-assignment stability indicator
  (stable / flips) so a marginal pairing is visible.

### I-5. Carbon & LCA
- **EN 15978/15804 module labelling**: present A1-A3, A4, D explicitly and state the avoided-burden
  (module D) convention; selectable datasets (ICE / Ökobaudat / product EPDs) with an uncertainty
  band propagated to the headline figure.
- **Report visuals**: per-assignment carbon "payback" bar and a whole-project waterfall
  (potential → screened → matched → net).

### I-6. Data & BIM round-trip
- ★ **Write-back to Revit**: a pyRevit **"Apply Matches"** button that colours reused members and
  sets `ReusedFrom` / provenance / passport-ID parameters in the new model. The killer
  thesis-defence demo, and it makes the tool feel like a product.
- **IFC coordinate export** (placement transforms) so the IFC path can run frame analysis.
- **Capture releases + rotation + grade** from Revit (feeds I-3 items directly).
- **Extractor emits the schedule CSV itself** → `steelreuse-validate --schedule` runs with zero
  manual steps (automates the Phase-1 completeness check forever).
- **IFC property-set export** of the material passport (ISO 20887 / Madaster-friendly fields).
- **`schema_version`** in the extraction JSON + a migration warning path.

### I-7. App & report UX
- ★ **Fuzzy-match review queue** in Streamlit: list quarantined names with their candidates,
  approve/reject buttons → writes the override CSV. Closes the only human loop that today lives in
  a bare CSV.
- **3-D model viewer** (plotly line segments from the coordinates we already carry) coloured
  reused / new / unknown — instant comprehension of the result.
- **Per-assignment calc sheet**: expandable full trace — every combination, every check, the
  numbers and the clause references. What a reviewing engineer actually wants to see.
- **Scenario compare**: run ±cutting / ±construction / ±connection-screen side by side in the app
  with a deltas table.
- **CSV/Excel export** of assignments + passport; an **engineer sign-off page** in the report
  (what was checked, what was not, what to verify manually before fabrication).

### I-8. Software quality & distribution
- ★ **Property-based tests** (`hypothesis`): encode invariants — a strictly bigger section never
  *increases* utilisation; knockdown scales f_y linearly; `χ ≤ 1`; the governing combination's
  utilisation ≥ every individual combination's. Catches bug classes example-based tests cannot.
- ★ **Catalog-wide differential validation**: sweep all 711 sections against independently
  recomputed resistances and spot-check published beam-load tables — today's hand anchors cover a
  handful of sections.
- **Golden-file regression**: a full-pipeline output snapshot on the bundled samples (byte-stable
  results guard against silent numeric drift).
- **mypy (strict) on `core/`** + a coverage threshold in CI; add **ubuntu** to the CI matrix and a
  wheel-install smoke job (`pip install dist/*.whl && steelreuse --demo`).
- ★ **Zenodo DOI + `CITATION.cff`** cut together with v0.2.0 — citable software in the thesis
  bibliography; optionally PyPI + a small mkdocs site for METHODOLOGY/VALIDATION.
- **Performance**: profile the 1000-member case; `lru_cache` the check hot path (section, fy,
  rounded demand) if the matcher matrix ever gets slow.

### I-9. Validation & academic credibility
- ★ **Reproduce an independently published worked example** (SCI P362 / Access Steel beam-column)
  as a test — external authority beyond our own hand algebra (thesis roadmap #12).
- **Cross-software benchmark**: the validated 2-bay frame solved in SAP2000 via the OAPI scaffold →
  force comparison table in thesis §11.
- **EU showcase case study** (IPE/HE building) alongside the US one — exercises the EU catalog and
  the EN grades end to end.
- **Thesis sensitivity study**: CO₂ saved vs knockdown / condition mix / γ-factors (tornado chart) —
  the question examiners reliably ask.
- **EN 1990 Annex D statistical f_y**: when coupon results exist (n tests, V_x known/unknown),
  derive the characteristic value statistically instead of applying a flat knockdown. Thesis-grade
  rigour and a natural PDA extension.

### I-10. Pre-demolition audit & material passport
- ★ **Literature-calibrated condition→knockdown factors** (SCI P427 protocol, prEN reuse drafts),
  with a citation per factor in METHODOLOGY §3.1 — today's values are representative defaults.
- **Damage/hole register** per member → net-section deduction (I-1) and sharper recoverable length.
- **Passport ID / QR per member** in the report — traceability from deconstruction through
  fabrication to the new frame.
- **Prior-use class** field (crane girder / dynamic loading history → fatigue screening flag).

---

## Done in Tier 1 (for reference)
- 🔴 Catalog/carbon CSVs moved into the package (`src/steelreuse/data/`) so an installed wheel finds
  them (was `parents[3]`, outside the wheel). Verified the wheel now bundles them.
- 🟠 Greedy fallback no longer books net-negative (CO₂-losing) matches — mirrors the MILP.
- 🟠 Booked/reported "CO₂ saved" is now the net figure the optimiser uses (includes connection
  refabrication carbon); off-cut is an explicit soft preference, not booked.
- 🟡 README/CLAUDE install command fixed (was missing the `report`/`bim` extras → Jinja2 absent →
  CLI crash at HTML render); stale paths and test counts refreshed; removed dead `STEEL_DENSITY_KG_M3`.
