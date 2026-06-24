# Future improvements & known limitations

> **This file is the live backlog of OPEN work only.** Completed work is not repeated here — it lives,
> dated and per-release, in [CHANGELOG.md](CHANGELOG.md), and is described in
> [docs/OVERVIEW.md](docs/OVERVIEW.md) §13 (the thesis roadmap + limitation register) and
> [docs/METHODOLOGY.md](docs/METHODOLOGY.md) (clause → code → test). **For the record of what is already
> done and why, read CHANGELOG.md first.** When the roadmap in OVERVIEW §13 and this file diverge,
> OVERVIEW §13 is the thesis-frozen snapshot and this file is the working list. When an item ships, move
> it to CHANGELOG and delete it here.

Severity: 🔴 blocks credibility / wrong result · 🟠 important methodology gap · 🟡 minor / cosmetic.

> **Certifiability & market positioning.** The goal-specific roadmap for making the tool *certifiable*
> (reproducible audit trail + auditable rules/provenance + per-member EN evidence) and positioning it
> against the 2026 startup field lives in its own file:
> **[docs/ROADMAP_CERTIFIABLE_REUSE.md](docs/ROADMAP_CERTIFIABLE_REUSE.md)** (the *what to build*), with
> the literature/competitor analysis and redefined scope in **[docs/POSITIONING.md](docs/POSITIONING.md)**
> (the *why*). That roadmap is the home for the evidence-package export, versioned rule-mapping +
> mismatch log, coverage/time-to-result metrics, marketplace interop adapters and SCI P427 / BS 8001
> alignment — they are **not** repeated below (this file stays the engine/methodology backlog).

> **Context for agents — what is already done** (do not re-propose): global frame analysis (gravity load
> path, EN 5.3.2 sway EHF + P-Δ, wind, EN 1998 lateral-force seismic, multi-span splitting, α_cr
> classification), full 6.3.3 biaxial interaction, shear–moment (6.2.8), construction-stage and
> wind-uplift cases, moment-shape `C₁`/`C_m`, HSS + hollow-section checks, UK UB/UC catalogue, geometry
> name-confirmation, pre-demolition audit, connection feasibility + standard fin-plate capacity screen,
> selectable objectives + Pareto, stock-stewardship knobs A1–C2, cutting-stock (default), match
> optimality + independent verifier, SAP2000 cross-software backend, sensitivity study, published
> JRC/ECCS validation, property-based tests, Revit write-back (Apply / Clear / Reuse Schedule / Trace /
> Compare Runs + run history). See CHANGELOG.md for each.

---

## Structural credibility — open residuals

- 🟡 **IFC profile/dimension coverage.** [ifc_extract.py](src/steelreuse/ifc_extract.py) now resolves
  member-axis coordinates (`start_xyz`/`end_xyz`) from the `ObjectPlacement` chain, so the IFC path can
  drive the frame solve. Remaining: measured section dimensions are read for **I-shapes only**
  (`IfcIShapeProfileDef`) — add `IfcRectangleHollowProfileDef` / `IfcCircleHollowProfileDef` /
  channel/tee profile defs so HSS, tube and channel get IFC geometry confirmation too. Until then
  those families have **no IFC-measured dims and rely on name match alone** — more likely to land in
  `fuzzy`/`unknown`.
- 🟠 **Cross-software IFC naming.** Non-Revit exporters (Tekla, Advance Steel, ArchiCAD, Bentley) name
  sections their own way, so a `.ifc` from another tool produces more unknown/fuzzy matches than the
  Revit path. The open UK `normalize_name` work (below) is part of this; a broader exporter-aware name
  normalisation (and a per-tool alias table) would raise the auto-map rate.
- 🟡 **Validate IFC ingestion per source tool.** The dashboard now ingests `.ifc` from any BIM tool, but
  IFC export quality varies wildly (coordinates / profiles / quantities / material grades present or
  not). Run a real export from each major tool (Tekla, Advance Steel, ArchiCAD, Bentley) end-to-end and
  record what survives — mapped vs fuzzy vs unknown, coords present, frame solve usable — as a coverage
  matrix, so the cross-software claim is evidenced, not assumed.
- 🟠 **Real frame moments.** `--col-ecc` is only a notional eccentricity lever; there is no real
  beam-to-column moment transfer / unbalanced-span / gravity-frame moment modelling.
- 🟠 **Semi-rigid connection sensitivity.** Beams are pinned and column bases fixed — an idealisation;
  real joints sit between pinned and fully rigid. The SAP2000 parity confirms the *solver*, not this
  assumption (both solvers share it). A rotational-stiffness sweep (pinned ↔ fixed bracket, or an
  EN 1993-1-8 joint classification) reporting how the governing moment moves with joint fixity would
  turn an assumed result into a bounded one.
- 🟡 **Real relative stiffness on redundant frames.** The generic stiff section is exact for determinate
  spans (forces are section-independent), and the lateral path uses real member `I`; but on
  *indeterminate / redundant* frames the force split depends on real relative stiffness. Confirm real
  `I` is used wherever a frame is redundant and flag any location where the generic section could bias
  the distribution.
- 🟠 **Member-level lateral combinations.** The member-level envelope ships gravity + the optional sway,
  construction-stage and wind-uplift cases; wind, seismic and **pattern** combinations are only on the
  frame path. Populate them as further envelope entries on the member-level path too.
- 🟠 **Modal / response-spectrum seismic.** The current seismic is the simplified EN 1998 lateral-force
  method with a user base-shear coefficient — no modal spectrum, accidental torsion, or `q`-factor/site
  spectrum. (`combos` is the hook.) A modal step would also derive `Cs` in-tool from `T₁` instead of
  taking it as input.
- 🟡 **Auto-idealisation of irregular multi-piece BIM.** Today the assembler either solves cleanly or
  falls back to analytic. Turning an arbitrary disconnected real model into a *well-conditioned* global
  frame is the open problem: per-piece solves, mechanism detection/repair, or user-guided supports.
- 🟡 **Effective length `k` from buckling modes.** `k = 1.0` system lengths are α_cr-verified and
  per-member overridable (`ky`/`kz`); inferring `k` from an actual buckling-mode / PyNite buckling solve
  is the remaining increment.
- 🟡 **Member rotation capture.** Read the Revit *Cross-Section Rotation* parameter so the local→section
  axis mapping is correct, closing the biaxial-orientation residual of the 6.3.3 check.
- 🟡 **Overhang edge rule.** The half-bay tributary edge rule assumes the slab edge sits at the perimeter
  columns (no cantilever); a real overhang adds load.
- 🟡 **Default-on geometry estimation.** `--trib-from-geometry` (and `--phi 1/200`) stay opt-in; flipping
  either on by default is a one-line change (both already fall back per-member where geometry is missing).
- 🟡 **Class 4 effective-section properties** (EN 1993-1-5 §4) so slender sections get a number instead of
  a `REVIEW` flag.

### Section-family expansion

The ranked list (open-web joists → round CHS/pipe → channels → angles → tees → cold-formed purlins) and
its value-vs-effort reasoning live in [OVERVIEW.md](docs/OVERVIEW.md) §13 — that is the canonical roadmap
home for this item. **Shipped (see CHANGELOG):** round HSS/pipe (CHS), channels (UPN), angles (L,
axial-only). Live notes on what is still open:

- 🟡 **Small EU sizes** (HEA/HEB/HEM 100–180, IPE80–140) — catalogue add, but **not a pure data drop**:
  the new-build baseline is the *lightest adequate* catalogue section, so adding smaller sizes lowers
  the avoided-new baseline for light slots and shifts the documented worked-example and stewardship
  carbon anchors. Add it as a deliberate change that re-derives and re-checks those anchors (or
  alongside the baseline-philosophy item below, which would make results catalogue-stable).
- 🟡 **Tees (WT/MT/ST).** Mono-symmetric with a special LTB rule; remain in `unknown` until that lands.
- 🟡 **Cold-formed purlins (Z/C).** Need EN 1993-1-3 effective-section checks; out of scope for now.
- 🟡 **Channels — extend the catalogue and the checks.** UPN (EU) ships; add UK PFC and US C/MC rows,
  and replace the doubly-symmetric `I_t/I_w` LTB approximation (currently warned) with a proper
  mono-symmetric `M_cr` (shear-centre offset, load position).
- 🟡 **Angles — bending capacity.** Axial (bracing/tie) ships; bending is flagged `REVIEW` rather than
  computed. A principal-axis (u/v) biaxial bending check would lift angles from axial-only to full.
- 🟡 **K-series open-web joists.** A fabricated *truss*, not a prismatic section — no single `A/I/W`
  represents one and EN 1993 has no OWSJ rules (capacity is by SJI load tables). Does **not** fit the
  `SectionProps` + EC3 engine. If added, it must be an *identify-by-designation + flag
  "uncheckable, verify by SJI table"* track that excludes joists from the EC3 supply — never a
  fabricated member-check number.

## Actions & combinations — open

- 🟡 **Pattern live load** for continuous beams on the frame path (alternate-span DL/LL).
- 🟡 **ψ-factors by occupancy category** (EN 1990 Table A1.1) instead of the fixed 0.7 / 0.3.
- 🟡 **Snow** (EN 1991-1-3) for roof members (highest-level beams are detectable).
- 🟡 **Wind-uplift residuals:** a separate roof permanent load (today the floor `dead_kpa` is reused) and
  uplift on the frame path as a real reversed load case (today an isolated-span envelope entry).
- 🟡 **Floor-vibration screen** (`f₁ ≈ 18/√δ_perm`, flag floors below ~4 Hz) — cheap, professional.

## Member checks — open

- **Net-section at existing bolt holes** (`0.9·A_net·f_u/γ_M2`): reclaimed members arrive with holes —
  add a per-end hole register to the PDA schema and deduct in tension/bending. Genuinely reuse-specific.
- **Corrosion section loss:** a PDA "measured thickness loss (%)" field scaling `t/A/W/I` per member
  before checking (today condition only knocks down `f_y`, not the right physics for section loss).
- **Web bearing/buckling at new support points** (EN 1993-1-5 §6): a re-supported reused beam may need
  stiffeners at the new bearing locations — flag where.

## Matching, carbon & LCA — open

- 🟠 **Avoided-new baseline: lightest-adequate vs as-specified.** Today the avoided-production baseline
  is the *lightest catalogue section that passes the slot* (`lightest_adequate_section` /
  `baseline_new_mass_kg` in `match/optimize.py`). This is the carbon-honest floor — it never credits
  avoided over-design — but it has two consequences: (1) the headline saving **depends on what sizes
  are in the catalogue** (adding smaller sizes lowers every light-slot baseline; see the EU small-fill
  note above), and (2) it can *under*-claim vs the member the engineer actually specified. The
  alternative is to baseline against the demand's own **modelled `design_section`** (already carried
  on the slot): catalogue-stable and "what would really have been built", but it credits avoided
  over-design and needs a design section present (load-only demand has none). A selectable
  baseline mode (lightest-adequate | as-specified) with the choice documented in METHODOLOGY would
  resolve both — and is the clean prerequisite for the EU small-fill. Decision, not a bug.
- ★ **Multi-objective optimisation:** CO₂ + cost (€/t reclaimed vs new + refab) + transport. Weighted-sum
  in the existing MILP first; pymoo NSGA-II for a true Pareto front later (the optional extra exists).
- ★ **A4 transport emissions:** donor-site ↔ new-site distance × mode factor, added to the passport and
  the optimiser's net figure (with an optional max-radius cutoff). The most-asked LCA question.
- **Donor splicing** (two donors → one long slot; the converse of cutting-stock, with a splice penalty +
  connection screen on the joint).
- **Same-section grouping preference:** a soft objective term rewarding repeated sections across slots
  (constructability / fewer connection types).
- **Residual-stock export:** unmatched donors → a CSV directly loadable as the *next* project's supply —
  circularity chaining, a strong thesis narrative.
- **Match robustness badge:** rerun the match at ±10 % loads and show a per-assignment stability indicator
  so a marginal pairing is visible.
- **EN 15978/15804 module labelling:** present A1–A3, A4, D explicitly with the avoided-burden (module D)
  convention; selectable datasets (ICE / Ökobaudat / EPDs) with an uncertainty band on the headline.
- **C3 — ML option-value calibration** ([docs/OPTION_VALUE_ML.md](docs/OPTION_VALUE_ML.md), designed, not
  built): the dormant `experiments/ml/` layer estimating per-stock-item option value from a demand
  distribution to *calibrate* the C2 reserve weight. Decision support, never a gate (DESIGN_PRINCIPLES
  rule 3). Storage is not free — transport/yard/double-handling stay a parameter, not an assumption.

## Validation & academic credibility — open

- 🟠 **Solver parity ≠ model fidelity (framing).** The SAP2000 cross-check now covers the lateral
  P-Delta path too — PyNite and SAP2000 agree exactly on gravity and to ≤ 0.12 % on the sway force
  path. This validates that PyNite solves the *idealised model* correctly; it does **not** prove the
  model equals reality (both solvers share the same idealisations). The fidelity ceiling is the
  modelling assumptions — semi-rigid joints, real relative stiffness on redundant frames, code load
  magnitudes (φ / wind / Cs as inputs), linear-elastic + P-Δ only (no material nonlinearity, no P-δ
  member curvature unless subdivided), and the reused-steel physics (coupon `f_y`, corrosion loss,
  fatigue history). Keep the "decision-support, not code-certified" framing; do not let "validated
  against SAP2000" be read as "validated against reality".

- **UK published resistance cross-check.** Export the Blue Book **"Member resistances"** tables and assert
  the tool's computed `N_b,Rd`/`M_b,Rd` for UK sections match the SCI EC3-UK-NA design values — upgrades
  the UK story from "validated method, applied to UK sections" to "matches the official UK design tables".
  (UK NA uses `γ_M1 = 1.0`, matching the tool; align the buckling length and curve.)
- **EU showcase case study** (an IPE/HE building) alongside the US one — exercises the EU catalogue and
  the EN grades end to end.
- **EN 1990 Annex D statistical `f_y`:** when coupon results exist (n tests, V_x known/unknown), derive
  the characteristic value statistically instead of a flat knockdown. Thesis-grade, a natural PDA extension.

## Pre-demolition audit & passport — open

- ★ **Literature-calibrated condition→knockdown factors** (SCI P427, prEN reuse drafts) with a citation
  per factor in METHODOLOGY §3.1 — today's values are representative defaults.
- **Damage / hole register** per member → net-section deduction (above) and sharper recoverable length.
- **Passport ID / QR per member** in the report — deconstruction → fabrication → new-frame traceability.
- **Prior-use class** field (crane girder / dynamic history → fatigue-screening flag).

## App & report UX — open

- ★ **Fuzzy-match review queue** in Streamlit: list quarantined names + candidates with approve/reject
  buttons that write the override CSV. Closes the only human loop that today lives in a bare CSV.
- **Per-assignment calc-sheet drill-down** (report and/or app): every combination, every check, the
  numbers and clause references — what a reviewing engineer wants to see ("trust me" → "verify me").
- **2-D match card per assignment:** side-by-side donor vs receiver elevation from data already held
  (lengths, depth/width, cut position, connection note) — eyeball the fit without opening either model.
- **3-D model viewer** (plotly line segments from the coordinates already carried), coloured
  reused/new/unknown. *Downgraded:* Revit write-back already colours the real model; only worth building
  for the no-Revit audience (the public web demo / thesis defence).
- **CSV/Excel export** of assignments + passport, and an **engineer sign-off page** in the report (what
  was checked, what was not, what to verify before fabrication).

## Software quality & distribution — open

- ★ **Catalog-wide differential validation:** sweep all 864 sections against independently recomputed
  resistances and spot-check published beam-load tables (today's hand anchors cover a handful).
- **Golden-file regression:** a full-pipeline output snapshot on the bundled samples to guard against
  silent numeric drift.
- **mypy (strict) on `core/`** + a coverage threshold in CI; add **ubuntu** to the CI matrix and a
  wheel-install smoke job (`pip install dist/*.whl && steelreuse --demo`).
- ★ **Zenodo DOI + `CITATION.cff`** cut with the next release — citable software in the thesis
  bibliography; optionally PyPI + a small mkdocs site for METHODOLOGY/VALIDATION.
- **Performance:** profile the 1000-member case; `lru_cache` the check hot path (section, fy, rounded
  demand) if the matcher matrix ever gets slow. <!-- ponytail: only if measured slow -->

## Deployment — open

- ★ **Public web demo:** deploy `app.py` (+ bundled demo data) to Streamlit Community Cloud or an HF
  Space — a live link for the thesis/CV instead of "clone and run".
- ★ **REST API (FastAPI)** wrapping `run_pipeline`, so pyRevit, a PDA mobile app, or a marketplace tool
  can call the matcher without a Python install.
- **Madaster / EPD-format passport export** (ISO 20887 schema) — claim compatibility with a real CE
  platform.
- **Q&A chatbot over the report** ("Why wasn't W12X26 #45 reused?") — retrieve the assignment/quarantine
  record and feed it (numbers fixed, not invented) to the existing narrative guard.
- **Desktop packaging — NOT PyInstaller.** Ship a portable folder (embeddable Python or the signed venv +
  a `run.bat` launching `streamlit run app.py`) — robust on the WDAC-locked box, no unsigned-binary
  issues. Revisit `stlite`/Pyodide only if the heavy deps ever become optional.

## New directions — beyond current scope

Capabilities the project doesn't touch today (vs refinements above):

- **Multi-donor stock / marketplace model.** Match against a *pool* of donor buildings (a regional
  steel-stock DB), picking the best donor across sites per slot — from "one-off reuse" toward a real
  circular supply chain.
- ★ **Cost / LCC layer.** Reclaimed material + refab/connection + transport vs new-build cost — a second
  axis for the multi-objective optimiser and something a QS could use.
- **Design-for-disassembly suggestions** for the *new* structure (bolted over welded) — closes the loop
  forward, not just backward.
- **Fabrication output / cut-list generator** per matched donor (cut lengths, end-prep, hole locations).
- **GIS layer for donor discovery** (if demolition-permit/registry data ever exists) — "help me find a
  donor", not just "I have one".

### CirCoFin / Circular Construction Hub alignment

CirCoFin (Horizon Europe) builds Circular Construction Hubs = a Physical Material Bank + Digital
Marketplace. They list *what's in stock* but cannot verify *whether a listed item is structurally usable*
— exactly this tool's EN 1993 + audit/passport layer. Aim: position the engine as the structural
feasibility layer a CCH is missing (a citable adoption story, a candidate CCH Toolbox module).

- ★ **Digital Marketplace listing export:** reshape unmatched donor stock (`PassportEntry` → sections,
  lengths, grades, condition/knockdown, location) into a CCH-marketplace listing format. Mostly data
  reshaping — the most direct interoperability win.
- ★ **CCH cost / feasibility module:** a cost dimension alongside CO₂ (avoided disposal, hub
  storage/handling, resale vs new) — speaks to CirCoFin's financing objective.
- ★ **Multi-donor pool matching (regional CCH model):** generalise one-donor → one-demand to a *pool* of
  sources → one demand. Largest lift; the real "this models a hub" shift.
- **Inventory dwell-time field** (date entered vs matched → "average days-to-reuse") once the pool exists.
- *Watch, don't build yet:* DIN/EU reuse-data-standard alignment (until CirCoFin publishes a schema).
