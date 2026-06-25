# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Moment-shape (C1/Cm) is now ON by default in the Run Match panel and the sweep base**
  (`steelreuse_panel.xaml`, `steelreuse_sweep_planner`). Real Revit runs now use the sharper, valid EN
  check (LTB moment-gradient C1 and 6.3.3 Cm from the actual moment diagram) instead of the
  conservative uniform-moment assumption. The **engine/CLI default stays off** so the validated case
  study still reproduces byte-identically; this only changes the UI defaults.

### Added
- **Donor splicing** (`--splice`, Scenario Sweep §4, opt-in). A long demand slot that **no single
  in-stock donor reaches** can now be filled by **two same-section, same-grade reclaimed pieces
  joined end-to-end** (one splice, two pieces; AISC 360 §J1.4 / EC3). The matcher generates genuine
  splice candidates (pruned to pairs where *neither* piece alone is long enough but together they
  reach the slot), adds binary splice variables to the MILP alongside the single-cell variables
  (with the slot/donor-use, cutting-stock length-cap, section-variety and max-min-utilisation
  constraints all extended to cover them), and the greedy fallback handles them too. A spliced reuse
  consumes both donor pieces in full and books a representative splice-joint carbon penalty
  (`SPLICE_PENALTY_KG`, ~30 kgCO2e) on top of the ordinary connection-refabrication penalty. The
  independent verifier re-validates spliced assignments (two-piece feasibility + both donors
  consumed) and the evidence package's per-assignment carbon reconciliation books the joint penalty,
  so a spliced result still certifies and reconciles. Exposed on the CLI and as a sweep run toggle;
  **off by default** (results byte-identical when off).
- **Balanced (max-min) utilisation objective** (`--objective balanced`, Scenario Sweep §5). A new
  policy objective that fills the most slots (the "members" primary) and then, among the
  maximum-count solutions, maximises the **worst** assignment utilisation — so no donor sits grossly
  under-used (at, say, 50 %) while another is at 100 %. Implemented as a single max-min MILP variable
  bounded below every selected pair (`t <= u_ij + (1 - x_ij)`), with the slot count kept strictly
  primary so evening-out never costs reuse. Selectable on the CLI, threaded through the what-if
  re-solves and the independent verifier (which judges it on the members primary), and usable as a
  sweep `objective` value; the CLI prints the achieved min/avg/max utilisation. Complements the
  existing `--w-overspec` / `--min-util` dials.
- **Selectable carbon-factor dataset + sweep axis** (`--carbon-dataset`, Scenario Sweep §4). The
  embodied-carbon factor set every saving is booked against is now a choice of three
  provenance-stamped, self-contained CSVs in `data/carbon/`: `ice_v3` (Circular Ecology ICE v3 2019,
  A1-A3 1.55 — the default, byte-identical to before), `ice_v4` (ICE v4 2024, 1.61 — the figure
  Climatiq surfaces as "Steel - Section") and `oekobaudat` (German structural-steel EPD via
  Ökobaudat / bauforumstahl-IBU, 1.74). The sets differ only in the A1-A3 *production* figure — the
  number EPD databases actually disagree on — while the reuse-process and end-of-life credits are
  held at common reference values (SCI P427 / worldsteel module-D / Cambridge-Allwood, orthogonal to
  the production database). The dataset threads through the whole pipeline (passport, match,
  disposition, marginal-value, diagnosis, alternatives), is exposed as a planner row + sweep axis
  (`carbon_dataset`), and is recorded — name, file, SHA-256, factor values — in the signed evidence
  package, so a result names the database it trusted.
- **`docs/SCENARIO_SWEEP_PLAN.md`** — the living plan for the sweep feature: the realism principle,
  the full set of engineer-selectable axes (built + to-build), the omitted dials, the utilisation
  question, the staged "funnel" sweep, the performance model, and domain notes (construction case,
  load take-down, carbon-factor source, splicing feasibility) gathered from the design discussions.
- **Scenario Sweep — the planner + trade-off board** (new `Match.panel/Sweep.pushbutton`,
  `steelreuse_sweep_planner`, `steelreuse_sweep_board`). A new Match-panel button opens a planner:
  lock the donor + demand, tick which dials to vary (objective, min-util, max distinct sections,
  knockdown) with a comma-separated value list each, see a live run-count (with a confirm above 60),
  and run every combination at once. Runs go through the lean orchestration core on a background
  thread (CPU-1 engine processes at a time) so Revit stays responsive, with per-point progress. When
  every point is done a **board** opens: one row per combination, **front-first** — the rows on the
  non-dominated trade-off front are highlighted (no single run beats them on every currency at once),
  failed points greyed, every column sortable. Each point is a normal `results.json` run, so "Open
  selected run folder" reaches its full output and the Compare / Results windows open it too. Value
  typing moved into the tested core as `sweep.parse_values`; the windows are thin Revit glue.
- **Scenario-sweep orchestration core (lean-first)** (new `steelreuse_sweep`). Backbone for turning
  one fixed base config + a few varied dials (objective, min-util, cutting, connection screen,
  knockdown, …) into many runs at once and ranking them, so the engineer stops hand-running the match
  and eyeballing diffs two-at-a-time. Pure, unit-tested pieces: `expand_grid`/`grid_size` (the
  cartesian product + a count for the planner's cap), `point_id`/`point_label`/`plan` (stable
  per-point folders), `lean` (strips the finalist-only audit add-ons — donor-value, verify-match,
  disposition — so sweep points are seconds, not minutes; keeps the cheap Pareto solve),
  `collect`/`rank`/`pareto_front` (read each point's `results.json` into a board record, order
  best-first per metric, keep only the non-dominated trade-offs). Plus `run_grid`, a bounded thread
  pool over an injected run function (defaults to `steelreuse_runner.run_match`) that runs
  `default_workers()` = CPU-1 engine processes at once, leaving Revit a core; `clamp_workers` caps a
  manual override at the logical core count, so a teammate on a smaller machine can't oversubscribe
  RAM by typing a big number (more workers than cores gives no speed-up anyway). Every point is a normal
  `results.json` run, so the existing Compare / Results windows open and drill into any of them. The
  WPF planner + ranked board, and an optional cells-once core speed-up, slot in behind this surface.
  Tested in `test_sweep.py`.
- **The Pareto tab now reads as a trade-off, not just three rows of numbers**
  (`steelreuse_result_tabs.pareto`). Each alternative goal shows, in parentheses, what choosing it
  would change in every currency relative to the objective the run actually shipped (marked `*`), and
  `#` flags the best value in each column — so "optimise for members instead of CO2" reads directly as
  "+1 member, −20.0 kg CO2e, −40.0 kg reused mass" instead of leaving the engineer to subtract the
  rows by eye. This is the cheapest slice of the A/B story: the data was already computed by the
  `--pareto` solve; only the presentation changed. Tested in `test_result_tabs.py`.
- **The Results window now shows the full set of review tabs** (`steelreuse_results_window`, new
  shared `steelreuse_result_tabs`). The saved-runs Results window — the main place to review a run —
  previously had only Assignments / By section / Donor provenance; it now also has **Unfilled +
  diagnosis**, **Warnings**, and (when the run produced them) **Disposition**, **Donor value**,
  **Pareto** and **Portfolio** tabs, matching the Run Match window. The tab bodies were extracted
  into a shared, unit-tested formatter module so both windows render identically from one source.
  Tested in `test_result_tabs.py`.
- **Both native windows gain the results-view features** (`steelreuse_panel`,
  `steelreuse_results_window`, shared `steelreuse_panel_model`). The Run Match and Results WPF
  assignments grids now colour-band **utilisation** by severity (ok / high / over-1.0) via the new
  `util_severity` field, the display-status filter gains a **contention** option (only matches whose
  next-best donor went elsewhere), a new **By section** tab rolls reuse up per donor section (count /
  CO2e saved / mean utilisation / off-cut) via `section_rollup`, the Results grid gains the **Next
  best** column, and both CSV exports gain the next-best donor / margin / used-elsewhere columns.
  Unit-tested in `test_panel_model.py`.
- **HTML report is rendered on demand, not written every run** (`cli.py --no-report`, `runner`,
  `steelreuse_panel`). A match run no longer drops a `report.html` on disk each time: the Revit Run
  Match window's **Open report (HTML)** button (and the Results window) render the report fresh from
  `results.json` only when asked, via the shared standalone-HTML writer. The CLI gains `--no-report`
  (the results JSON / evidence / apply-matches / mismatch outputs are still written); the terminal
  default still writes `report.html` unless `--no-report` is passed.
- **Results view filtering/visual upgrades** (`steelreuse_results_view.py`). The assignments table
  now colour-bands **utilisation** by severity (low / ok / high / over 1.0), the status filter gains a
  **contention** option (show only matches whose next-best donor went elsewhere), a **Copy table as
  CSV** button copies the currently-filtered rows to the clipboard, and a **Reuse by donor section**
  roll-up summarises count / CO2e saved / mean utilisation / off-cut per section. Unit-tested in
  `test_results_view.py`.
- **Results window now mirrors the full run** (`steelreuse_results_view.py`). The HTML Results view
  showed only assignments / unfilled / quarantine / provenance; it now also renders the **warnings**
  flags (LTB restraint-reliant, imperfection-governed, connection-review, cut donors + remainder,
  unidentified types), the **"why" diagnosis** box (binding constraint + lever), and — when the run
  computed them — **stock disposition**, **donor what-if value**, **objective trade-off (Pareto)**,
  **portfolio** and **pre-demolition audit** sections, so the window is a complete review surface.
  Plus usability: a **reuse-rate bar** and extra KPIs in the header, **click-to-sort** assignment
  columns (alongside the existing filters), and a positive note when no slot went unfilled. The Run
  Match window also gains a **Donor what-if value** checkbox so its Tier-4 "Donor value" tab can be
  populated. Unit-tested in `test_results_view.py`.
- **Per-element "why" on every empty slot and every unused donor** (`match/optimize.py`). The match
  diagnosis no longer collapses into a single headline: `diagnose_match` now returns
  `unfilled_reasons` — one element-specific verdict per empty slot (its id, member, section, length,
  and `length` / `capacity` / `contention` / `economics` reason with a one-line explanation). And
  `stock_disposition` now tags each unused donor with **why it went unused**, judged against the
  *whole* slot set rather than only the unfilled ones: `too-short` / `too-weak` (never feasible),
  `contention` (feasible — even economic — somewhere, but a better donor won that slot; the slots are
  listed) or `uneconomic` (feasible but every fit books negative net CO2). Both surface through the
  pipeline: the HTML report gains a per-slot reasons table and a why-unused breakdown, the Revit
  results.json/Results window show the per-slot reason and a disposition reason summary, and the
  evidence package carries the full diagnosis + per-donor disposition. Numbers stay computed in Python
  (the LLM only renders them). Unit-tested in `test_narrative.py` and `test_stewardship.py`.
- **Per-assignment "next best alternative"** (`assignment_alternatives` in `match/optimize.py`). For
  every reused pair the matcher now records the **runner-up donor** for that slot, the net-CO2
  `margin_kg` of the chosen donor over it, and whether that runner-up was itself reused elsewhere —
  which explains the optimiser's local choice ("D1 won N2#0 over D3 by 8.6 kg; D3 went to a better
  slot"). It ranks the substitutes for a slot, so `margin_kg` reads as kgCO2e under any objective and
  can be negative (the runner-up out-scored the chosen donor locally but was more valuable
  elsewhere); it is *not* a re-solve with a donor removed (LP shadow prices stay out of scope). Always
  computed (only the filled slots are re-derived, so it is cheap) and surfaced as a "Next best" column
  in the HTML report, the Revit results.json/Results assignments grid, and the evidence package.
  Unit-tested in `test_narrative.py`, `test_panel_model.py` and `test_evidence.py`.
- **Per-donor "what-if" marginal value** (`donor_marginal_value` in `match/optimize.py`; opt-in via
  `--donor-value` / `run_pipeline(donor_value=True)`). Where the next-best column answers a *local*
  question, this answers the *global* one: for each REUSED donor the whole match is re-solved with it
  deleted from the stock, and the drop in total booked CO2 is its true marginal value — the concrete,
  re-solved analogue of an LP shadow price, honest about the integer problem (it includes the whole
  cascade: the runner-up freeing up, that slot's loser moving, and so on) and verifiable by re-running
  the solver rather than trusting a dual. A small value means a close substitute exists; a large one
  means the result leans on that donor. Only reused donors are analysed (an unused donor's marginal
  value is zero by construction), ordered by booked CO2. It is the one genuinely expensive advisory —
  one MILP solve per donor — hence off by default. Surfaced as a console summary line, a "Donor
  what-if value" report table (donor, marginal value, slots lost, cascade size), a "Donor value"
  Revit results tab, and a `donor_marginal_value` block in the evidence package. Unit-tested in
  `test_stewardship.py` and `test_panel_model.py`.
- **Spreadsheet inventory input (`.csv` / `.xlsx`) + a blank template** (`inventory_sheet.py`). A
  donor or demand model no longer has to be extractor JSON: a `.csv`/`.xlsx` list of members is read
  straight into the schema (headers matched by alias — `section`/`profile`, `grade`, `length`,
  `member type`, `provenance`…; values coerced/normalised; blank rows skipped), so a stockist or
  engineer with no Revit/IFC model can still drive the matcher. `steelreuse --inventory-template
  <path>` writes a **blank template** — column headers, one worked example row, and the conservative
  `unverified` provenance flag (the `.xlsx` form adds a *Guide* sheet documenting every column). The
  reader is one dispatch point (`load_model_file`), so it applies to the matcher, portfolio, sensitivity,
  value-case and validate alike. In Revit: a new **Extract → Inventory Template** button writes the
  template; the **Run Match** window's Donor/Demand pickers and the shared donor picker (Value Case /
  Review) now accept `json|csv|xlsx`, with a *Blank inventory template…* shortcut under the model
  boxes. `.xlsx` needs the optional `xlsx` extra (openpyxl); `.csv` needs nothing. Unit-tested in
  `test_inventory_sheet.py`.
- **Revit surfacing of the evidence package + mismatch log (Run Match / Results buttons)**. The Run
  Match runner now always emits `evidence.json` (Roadmap §1.1) and `mismatch.csv` (§1.2) alongside the
  report/status/results in each run folder, so every run is self-contained — no new button. The
  results.json contract carries the rule-data **versions** + the full **donor mismatch log** (summary +
  per-row), and the per-run `paths` block points at the evidence/mismatch files. The existing
  **Results** window surfaces them: the header names the ruleset version + donor-provenance counts,
  and *Open report* renders the rule stamp + a per-donor "classified-with-a-reason" provenance table
  (*Open folder* reveals the signable `evidence.json`). All wiring is in the pure, IronPython-safe
  view-model / HTML view / runner, so it is unit-tested without Revit (`test_results_provenance.py`).
- **Externalised, versioned rule data + donor mismatch log** (`core/rules.py`, `core/mismatch.py`,
  `data/rules/`; Roadmap §1.2). The judgement / standards-derived values a reviewer must trust or cite
  now live in version-stamped CSV files with `# version:` + `# source:` provenance headers, not buried
  in code: nominal f_y by grade + EN thickness band (`material_grades.csv`), the ASTM grade-default
  priority table (`grade_defaults.csv`), and the condition / verification → f_y knockdowns
  (`condition_knockdown.csv`, `verification_knockdown.csv`); the carbon factors gained the same header.
  `core.rules` loads them (reproducing the previous hardcoded values exactly — no number drift) and
  exposes a manifest (versions, sources, SHA-256) stamped into the evidence package. Internal solver
  tuning (off-cut weight, cut tolerance, over-spec ratio, knockdown floor, fuzzy cutoff) is left in
  code — it is implementation, already logged via `MatchResult.weights`, not a citable rule.
  - **Mismatch log**: every donor row is classified `mapped` / `fuzzy` / `unknown` / `quarantined`
    with a reason — 100% of donor rows accounted for, so nothing is silently dropped. Built from the
    section mapping (`ValidationReport.by_member_id`, new) + the pre-demolition audit, cross-referenced
    with the match for the reused/unused outcome. Surfaced in the evidence package and via console
    summary; CLI `--mismatch-csv` exports the per-row table.
- **Per-run evidence package** (`evidence.py`, `cli.py`; Roadmap §1.1). One signable JSON per
  matching run that bundles everything a reviewer needs to re-check the result without redoing the
  maths: every input model (donor + demand, with SHA-256 hashes) and the *resolved* supply/slots;
  the tool version, section-catalogue + carbon-factor hashes/values, the objective and every economic
  weight; per-assignment **EN 1993 pass-evidence** (governing clause, utilisation, χ_LT, governing
  load combination, section class, f_y) plus a carbon breakdown; the **`verify_match` certificate**
  (feasible + no improving single move), computed at build time; and a **carbon reconciliation**
  proving the per-assignment savings sum to `MatchResult.total_co2_saved_kg`.
  - Round-trippable: `rebuild_run` + `verify_from_package` re-run the independent audit from the
    package alone and return the same verdict. CLI `--evidence-out PATH`; auto-written for `--demo`
    (`reports/demo_evidence.json`). Every figure is re-derived from the same kernels the solve used
    (`check_member`, `verify_match`, `baseline_new_mass_kg`) — assembly + a stable schema, not new
    science.
- **Zone-based loads** (`core/loads.py`, `pipeline.py`, `cli.py`, panel). Each member is assigned a
  load zone (roof vs floor, auto by elevation via `assign_zones`, with per-member overrides) and each
  zone carries EN 1991-1-1 occupancy pressures, replacing the single hardcoded office pressure that
  over-loaded light buildings and rejected reusable donors.
  - `OCCUPANCY_PRESETS` covers every EN 1991-1-1 use category A–K (q_k from Tables 6.2/6.8/6.10;
    g_k a typical buildup assumption, overridable). CLI `--occupancy` / `--roof-occupancy` /
    `--zone-override`; panel gains Floor/Roof occupancy dropdowns.
  - **EN 1991-1-1 §6.3.1.2 αA/αn imposed-load reduction** (large areas; columns under many floors),
    reducing the imposed term only — a direct reuse gain for heavy multi-floor columns.
  - **Behaviour change (on by default):** the top framing level now uses a light roof (`roof-H`) and
    imposed loads are reduced — both reduce over-loading, so more donors qualify. Reproduce the old
    baseline with `--roof-occupancy office-B --no-load-reduction`.
- **Section-family expansion — round hollow (CHS), channels, angles** (`core/sections.py`,
  `core/ec3_checks.py`, `match/optimize.py`, `data/sections/`). Three new families ingest, classify,
  and check end-to-end alongside the existing I/H + rectangular-hollow stock:
  - **CHS (round HSS / pipe).** AISC v15 round HSS + Pipe (`us_round.csv`) and EN 10210 hot-finished
    CHS (`eu_chs.csv`), loaded by `load_catalog_round`. EN 1993-1-1 `D/t` classification
    (`chs_class`, Table 5.2 sheet 3), shear area `A_v = 2A/π` (6.2.6(3)), no LTB (axisymmetric),
    conservative buckling curve c. Names map via a 2-token round-HSS pattern and a `CHS` token
    detector (`HSS6.625X0.280`, `CHS168.3X6.3`, `168.3X6.3 CHS`).
  - **Channels (UPN).** `channels.csv` with single-outstand-flange classification (`channel_class`);
    LTB uses the doubly-symmetric `I_t/I_w` approximation, explicitly warned as mono-symmetric.
  - **Angles (L), axial-only.** `angles.csv` carrying a real principal `i_min` (`i_v`). Compression
    buckling about the principal minor axis (`N_b_Rd_minor`, curve b); **bending demand is flagged
    `REVIEW`, never given a capacity number** (principal-axis biaxial response is not auto-checked).
  - New `SectionProps.i_min` (principal minor radius of gyration; defaults to `min(iy, iz)`) and
    `is_round`. The new-build baseline search is confined to the slot's **shape family**
    (`_shape_family` in `match/optimize.py`: open I/H · hollow · channel · angle), so a channel or
    angle can never become the avoided-new baseline for an I-section slot (existing I/H and tube
    results are unchanged). Catalog consistency test made family-aware (centroid-offset `Wel`
    relations apply only where valid; the ERW `/0.93` mass basis is US-rectangular-HSS only). Guarded
    by `tests/test_sections.py`, `tests/test_ec3.py`, `tests/test_hss.py`. Tees, cold-formed Z/C,
    K-series joists, and the EU small-size catalogue fill remain out of scope (see
    FUTURE_IMPROVEMENTS).
- **UK section name normalisation** (`core/sections.py` `normalize_name`): real UK Revit/IFC
  designations now ingest end-to-end without manual overrides. `UKC 305x305x97`, `305x305x137 UC`,
  `UB 457x191x74`, and the `UKB`/`UKC` prefixes canonicalise to the catalogue form (`UC305X305X97`,
  resolving as `normalized`, confidence 1.0). UK detection runs before AISC so the trailing `C` in
  `UKC…` is not mis-read as an AISC channel; the `UB`/`UC` token must lead at a word boundary so
  `TUBE 100x100x5` is not read as `UB100x100x5`. Guarded by `tests/test_sections.py`
  (`test_normalize_name_uk`, `test_uk_does_not_hijack_hss_or_eu`).
- **Dashboard ingests IFC from any BIM tool.** The Streamlit app (`app.py`) now accepts an `.ifc`
  upload (donor and/or demand) alongside extraction JSON: the file is run through the Revit-free IFC
  extractor on upload (`extract_ifc` → schema JSON → pipeline), so Tekla / Advance Steel / ArchiCAD /
  Bentley models can be matched without Revit or the CLI. Needs the `[bim]` extra (ifcopenshell); a
  missing extra or unreadable model is reported in the UI rather than crashing. The IFC path resolves
  member-axis coordinates (so the frame solve runs) and reads measured dimensions for I-shapes.
- **Revit run history + Compare Runs panel** (`pyrevit_extension/.../lib/steelreuse_runs.py`,
  `steelreuse_compare.py`, `CompareRuns.pushbutton`): every match run is auto-saved under a name
  (its `results.json` copied into a history folder under a run id; reloadable / deletable). A new
  **Compare Runs** window reads that history and sets scenarios side by side without re-running the
  engine — an N-run **KPI table** (members reused / CO₂e saved / mass reused / distinct sections /
  unfilled slots) plus, for exactly two runs, a **per-slot diff** of what changed. Pure view over the
  headless result model. See OVERVIEW.md §10.2.1.
- **Sensitivity & uncertainty study** (`steelreuse-sensitivity`, `src/steelreuse/sensitivity.py`): a
  one-at-a-time **tornado** + optional **Monte-Carlo P5–P95 band** of net CO₂ saved over knockdown /
  γ-factors / loads / end-of-life counterfactual. Re-runs `run_pipeline` and reads its result (no
  arithmetic of its own, so the no-LLM-math rule holds). Headline finding: the end-of-life
  counterfactual dominates the uncertainty. Direction-guarded by `tests/test_sensitivity.py`. See
  METHODOLOGY §10.1.
- **Independently published worked examples** (`tests/test_published_examples.py`): reproduces three
  **JRC/ECCS** examples (Brussels 2014, "Design of Members") — HEB340 buckling, IPE400 `M_c,Rd`/
  `V_pl,Rd` + LTB constants, HEB320 **6.3.3** (Annex B `k_yy`/`k_zy`) — external authority beyond the
  project's own hand algebra. See VALIDATION.md §6.
- **UK UB/UC catalogue** (`data/sections/uk_sections.csv`, `standard="GB"`): 153 sections from the
  SCI/Tata Blue Book (EC3 UK NA / BS EN 10365), converted from the raw exports by
  `scripts/convert_uk_bluebook.py`, every row validated by the property-consistency test. Catalogue is
  now **864 rows** (40 EU + 283 US W + 388 US HSS + 153 UK). UB/UC reuse the doubly-symmetric I-section
  code path, so the JRC validation covers their checks. See METHODOLOGY §3.
- **Property-based / adversarial test suite** (`tests/test_properties_*.py`,
  `docs/PROPERTY_TEST_FINDINGS.md`): fixed-seed `hypothesis` properties over the EC3 checker, the
  matcher (independent re-verifier), the area-load model, frame topology, and schema robustness.
  **Caught a real bug:** the cl. 6.2.8 shear–moment check returned a *negative* utilisation under shear
  overload (`V_Ed > V_pl,Rd` left `ρ = (2V/V_pl − 1)²` uncapped) — fixed in `core/ec3_checks.py` by
  capping `ρ ≤ 1.0`, with a focused regression in `tests/test_ec3.py`.

### Removed
- **Repository hygiene for delivery.** Removed the local-only `maid/` dev-automation worker and
  `build_thesis_pdf.py` (with its release-workflow step and ruff exception) from the tracked tree —
  neither is part of the shipped tool. Pruned the superseded `*_test2`/`*test3` Revit extraction
  fixtures (~1.8 MB); the single canonical extraction now lives in `data/case_study/{donor,demand}.json`
  (renamed from `pyrevit_extension/{donor,demand}_test_4.json`). All runnable references updated.

### Changed
- README: added CI + license badges and corrected the status table — the SAP2000 backend is now shown
  as shipped-but-experimental (◑) rather than not-started (⬜).

### Added
- **Experimental SAP2000 (OAPI) frame backend + cross-software benchmark** (see OVERVIEW.md §11).
  An optional, OFF-by-default `analyze_frame_sap2000` ([core/frame_sap2000.py]) drops in
  for the PyNite `analyze_frame`: it reuses the *same* pure-Python topology and force-extraction
  helpers, swapping only the solver, so a force difference is solver numerics rather than modelling.
  Scope is the **ULS gravity** combination on connectable frames; sway/wind/seismic/P-Δ are refused
  (`ok=False` + warning) and SAP2000 being unavailable falls back to analytic exactly like a missing
  PyNite. Reachable via `--solver sap2000` (default `pynite`, so certified results are byte-identical)
  and through the `[sap2000]` extra (comtypes; Windows). The only sign-critical mapping (SAP2000 is
  tension-positive, EN/PyNite compression-positive) lives in a tested adapter. New
  `steelreuse-bench-sap2000` writes `docs/benchmark/forces_compare.{csv,md}` comparing analytic vs
  PyNite vs SAP2000 on a validated 2-bay frame; `tests/test_sap2000_parity.py` asserts PyNite↔SAP2000
  agreement and **skips** when SAP2000 is absent (CI stays green). On the bundled run, beams match
  analytic↔PyNite at 0 % (the `wL²/8` anchor) and the interior column carries 2× the load path.
  **Validated on real SAP2000 27.1.0 (2026-06-14):** the parity test passes — SAP2000 reproduces the
  PyNite forces to ~14 significant figures. A `--demand <model>.json` option runs the same diff on a
  real extracted building (PyNite vs SAP2000, with a worst-offenders summary). Instance teardown is
  hardened with a tracked-PID force-kill + watchdog, since SAP2000's headless `ApplicationExit` and the
  save-before-analyze step otherwise leaked/hung instances.

### Changed
- **Cutting-stock is now the DEFAULT** (CLI, app, `run_pipeline`): reclamation practice cuts
  members to length routinely, and the one-piece rule artificially stranded long donors (an
  18.8 m W14X109 could fill one 7.6 m slot and waste 11 m). `--no-cut` (app checkbox) restores
  whole-member-only reuse; `--cut` is kept as a harmless no-op. The low-level `match()` kernel
  keeps `allow_cutting=False` as its neutral baseline. Real test-4 case study moves from
  50 reused / 39.3 t to **71 reused / 60.6 t** (54 donors cut, ≈ 160 m reusable remainder);
  the demo headline is unchanged. CASE_STUDY/METHODOLOGY/README updated.

### Changed
- **The report narrative now diagnoses the result instead of reciting counts.** `diagnose_match`
  (`match/optimize.py`, computed on every report) classifies each unfilled slot — **length**
  (adequate sections in stock but too short / long-and-strong donors exhausted → splice or source
  longer stock), **capacity** (nothing strong enough), **contention** (a usable donor went elsewhere),
  **economics** (only over-spec donors fit, so reuse would lose carbon) — and names the **binding
  constraint** and the **lever**. It also flags **over-spec ("upgrade") matches** — reused donors
  ≥ 2× heavier per metre than the lightest section that would have passed (e.g. *a W30×235 where a
  W27×84 suffices*), honest under avoided-new but a stewardship signal (`--w-overspec`/`--reserve`).
  Both the deterministic narrative and the LLM prompt lead with that
  analysis and flag risks (LTB-restraint-reliant beams), e.g. on the real case study: *"the binding
  constraint is length … splicing two short members into one full length (or sourcing longer stock) is
  the lever; cutting is already applied."* Numbers stay Python-computed (`PipelineResult.diagnosis`,
  `reuse_rate_pct`), so the anti-hallucination guard is unaffected.

### Added
- **Moment-shape-aware `C₁` and `C_m`** (`--moment-shape`, `run_pipeline(moment_shape=)`, default off →
  byte-identical): drops the conservative uniform-moment assumption (`C₁ = C_m = 1.0`) when the real
  moment diagram is known. `C₁` (LTB moment-gradient) uses the general 4-moment / `C_b` formula — `1.0`
  for uniform moment, **`1.136`** for a simply-supported beam under UDL — and feeds `M_cr`/`χ_LT` for a
  less-conservative LTB check; `C_m` (6.3.3) uses Annex B Table B.3 `0.6 + 0.4·ψ` from the end-moment
  ratio for end-moment-driven members. The analytic path uses the simply-supported-UDL shape, the frame
  path samples the **solved** PyNite diagram (`x = 0, L/4, L/2, 3L/4, L`), and the unrestrained
  construction-stage / wind-uplift entries take `C₁ = 1.136`. Hand-verified vs EN 1993-1-1 Annex B / NCCI
  SN003 / AISC `C_b`; see METHODOLOGY §5.5b. On the real case study the headline is unchanged
  (slab-restrained, length-limited); under `--construction` the booked CO₂ moves 64404.2 → 63458.2 kg
  (reuse 71, verified) as the avoided-new baseline also lightens.
- **Stock stewardship & counterfactual fates** — a family of opt-in knobs (all default off, so existing
  results stay byte-identical) addressing what the single-project matcher cannot see: the donor's
  end-of-life fate, capacity waste, section variety, and future demand. See METHODOLOGY §6.1 + §7.6.
  - **A1 — end-of-life counterfactual** (`--counterfactual none|recycling|rerolling`): books reuse
    savings *net of* the foregone EAF-recycling (≈0.55 kgCO₂e/kg) or pilot-scale re-rolling
    (≈1.0 kgCO₂e/kg) credit the consumed steel would otherwise have earned — answering the standard
    LCA critique of avoided-new accounting. Credits are parameters in `data/carbon/factors.csv`; the
    mode + credit travel on the result so the verifier and Pareto table share the basis.
  - **A2 — stock disposition advisory** (`--disposition`, `--disposition-csv`): for every unused donor,
    compares *store* / *re-roll* / *recycle* with numbers and reports the best fate. Advisory only — the
    match is unchanged.
  - **B1 — utilization floor** (`--min-util x`): refuses pairs below a governing-utilization floor so
    grossly over-spec donors stay in stock (hard gate).
  - **B2 — over-spec soft penalty** (`--w-overspec w`): the capacity analogue of the off-cut
    preference; charges the score (not booked CO₂) for a donor's excess mass-per-metre over the slot's
    avoided-new baseline, flipping the "Frankenstein receiver" toward the lighter section.
  - **B3 — section-variety cap** (`--max-distinct-sections N`): anti-Frankenstein consolidation onto at
    most N donor section families (binary `y_f` in the MILP; greedy refuses an (N+1)-th family).
  - **C1 — portfolio matching** (`--demand a.json b.json …`): one MILP allocates the donor stock across
    several demand models at once, with per-project and global reporting — the principled "save it for
    the project that needs it". The single-demand path is unchanged.
  - **C2 — scarcity / option-value reserve** (`--reserve w`, EXPERIMENTAL, score-only): a single-project
    proxy for option value that holds scarce, versatile stock back from slots an abundant family could
    also serve. The principled tool is C1; a non-circular ML calibration of the weight is designed (not
    built) in `docs/OPTION_VALUE_ML.md` (**C3**).
- **Selectable matching objectives** (`--objective {co2,members,mass}`, `run_pipeline(objective=)`,
  app selectbox): the matcher can now maximize net CO₂ saved (default, unchanged), the **number of
  members reused**, or the **reclaimed steel mass put back to work** — the latter two break ties
  toward CO₂ and may select carbon-negative reuses when that serves the goal (booked CO₂ stays
  honest). Feasibility is identical across objectives; the MILP optimality proof, the greedy
  fallback, `verify_match` (which now judges improving moves by the result's own objective) and
  the CLI/report optimality wording all follow the chosen goal. On the real test-4 case study:
  co2 50 reused / 39.3 t, members 54 / 44.1 t (it consumes the long heavy stock the off-cut
  stewardship term conserves), mass 54 / 36.5 t.
- **Objective trade-off table ("Pareto view", `--pareto`, app checkbox,
  `run_pipeline(pareto=True)` → `PipelineResult.pareto`):** re-solves the same feasible pairs
  under every objective and shows members reused / CO₂ booked / steel mass reused per goal in the
  console and an "Objective trade-off" report section — the shipped assignments still follow
  `--objective`. On test-4 it surfaces that the CO₂-vs-members tension vanishes under `--cut`
  (71 reused either way): it is the one-piece-per-donor off-cut preference, not carbon physics.
- **Match optimality surfaced + independent verification** (`MatchResult.proven_optimal`,
  `verify_match`, CLI `--verify-match`): a proven-`Optimal` CBC solve is reported as exactly that —
  the best possible net-CO₂ assignment under the use constraints — in the CLI ("Matching:" line) and
  the report footer, while a greedy-fallback result is plainly labelled "not proven optimal". The
  new audit independently re-derives every feasible (donor, slot) cell and checks the use
  constraints, re-validates each assignment's feasibility and score, and confirms no improving
  single move exists (free donor → unfilled slot, or beating a chosen donor on its slot). The
  pipeline result now carries the admitted supply (`PipelineResult.supply`) so the audit runs
  without re-running the pipeline. Verified clean + proven optimal on the real case-study run.
- **Wind-uplift load-reversal case** (`--wind-uplift q`, CLI + app): net upward EN 1991-1-4 roof
  suction adds an envelope entry for roof beams (top framing level, located from coordinates) with
  the net upward line load `γ_Q·W_up − 1.0·g_k` (EN 1990 6.10, permanent favourable, imposed absent)
  checked with the **bottom flange in compression and unrestrained** — the load-reversal blind spot
  of the restrained-flange default. No reversal (net ≤ 0) changes nothing; default off. End-to-end
  test: an IPE300 donor that passes gravity restrained is rejected for a light 6 m roof slot under
  8 kN/m² suction (`M = 155.25 kNm > M_b,Rd ≈ 77.7 kNm`).
- **Trace Match button** (`pyrevit_extension/.../Match.panel/TraceMatch.pushbutton`): select a
  matched element on either side and jump to its partner(s) — a donor's slot(s) in the new design,
  or the donor member(s) filling a demand element. Partner ids are parsed from the "Reuse Paired
  With" parameter Apply Matches wrote, the open documents are searched for them (preferring one
  whose elements reference the source back), and the paired model is activated with the partner
  element(s) selected and zoomed to. Both models must be open in the same Revit session.

### Fixed
- **`run_pipeline(frame_analysis=True)` no longer silently skips the frame solve when no load model
  is passed.** The frame gate required an `AreaLoadModel` instance, so an API caller omitting
  `loads` fell back to analytic forces without any indication and got different results than the
  CLI (which always passes one). Now `frame_analysis` defaults the loads to `AreaLoadModel()` (the
  CLI default), and an explicit legacy flat `LoadModel` raises a clear `ValueError` instead of
  being ignored — a frame solve has no floor pressure to distribute from a flat per-member UDL.
- **Analytic path: span joints are now verified against column geometry** (`pipeline._verified_spans`).
  The extractor splits a demand beam at *every* crossing member endpoint (the frame solver needs those
  nodes), so a girder receiving joists arrived as e.g. five 1.5 m "spans". The analytic path then
  checked each as an isolated simply-supported span — understating the girder moment (`M ∝ L²`, ~25×
  on a 7.6 m girder) and emitting short slots no single member could fill. Span joints with no column
  endpoint at them are now merged back before checking (on the real case-study demand model, all 42
  multi-span members merge to their true single span). The frame path already verified supports
  physically and is unchanged; models whose columns carry no coordinates keep the extracted spans.

### Added
- **"Apply Matches" Revit write-back** (`steelreuse.writeback.build_writeback`, CLI
  `--apply-matches-out status.json`): reshapes a `PipelineResult` into a per-element status map
  (donor: reused/available/quarantined/unmapped; demand: filled/partially_filled/unfilled/non_steel),
  each with a colour and a one-line note. A new pyRevit **Apply Matches** button
  (`pyrevit_extension/.../Match.panel/ApplyMatches.pushbutton`) reads this JSON and applies a
  solid-colour graphic override + a "Comments" summary to the matching elements in the active view.
- **Write-back QoL**: the status JSON gains a `summary` block (per-status counts, slots filled,
  CO₂e saved) which Apply Matches prints as a headline inside Revit; the button output lists
  quarantined / partially-filled / unfilled elements as clickable select-and-zoom links (capped at
  25 per status); and a new **Clear Matches** button undoes a run — resets the colour overrides in
  the active view and removes only the SteelReuse data, leaving everything else intact.
- **Reuse passport in the model (shared parameters + schedule)**: Apply Matches now creates and
  fills schedulable instance parameters on structural framing/columns — "Reuse Status",
  "Reuse Paired With", "Reuse CO2 Saved (kg)", "Reuse Note" (definitions in
  `pyrevit_extension/steelreuse_shared_params.txt` for stable GUIDs; "Comments" is no longer
  touched). The writeback JSON carries structured `paired_with`/`co2_saved_kg` per element,
  cutting-stock aware (a cut donor lists every slot it fills, savings summed). A new
  **Reuse Schedule** button creates the "SteelReuse Passport" multi-category schedule (filtered to
  reuse-tagged elements, sorted by status, grand total on the CO₂ column).
- **Sway-stiffness classification (α_cr) + per-member effective-length override**: whenever the EHF
  sway imperfection runs, the frame computes EN 1993-1-1 5.2.1(4)B `α_cr = (H/V)·(h/δ)` per storey
  and direction from the sway drifts (`FrameResult.alpha_cr`); `α_cr ≥ 10` is reported as non-sway
  (the checker's `k = 1.0` system-length route is thereby *verified*, EN 5.2.2), `< 10` warns
  sway-sensitive, `< 3` demands a dedicated global-stability verification. New optional `ky`/`kz`
  fields on demand members override the buckling-length factors per member through both the analytic
  and frame paths. On the real case study the bare steel skeleton returns α_cr ≈ 0.2, correctly
  exposing that the model carries no lateral system of its own.
- **Shear–moment interaction (cl. 6.2.8)** (`core/ec3_checks.py`): above `0.5·V_pl,Rd` the bending
  check uses the ρ-reduced resistance — eq. (6.30) for rolled I/H, the conservative `(1−ρ)·M_c,Rd`
  for hollow sections — with peak M and V treated as coincident (conservative for a UDL span).
  Hand-verified (IPE300 S275, V_Ed = 300 kN → ρ = 0.223, M_y,V,Rd = 164.2 kNm).
- **Connection capacity screen** (`standard_shear_capacity` in `core/connections.py`): every
  open-section donor gets a lower-bound *standard fin-plate* shear resistance (single row of M20 8.8
  bolts in a 10 mm S275 plate; rows from the clear web depth; per bolt the EN 1993-1-8 Table 3.4
  minimum of bolt shear and bearing, conservative α_b = 0.5 — IPE300 → 3 rows ≈ 183 kN,
  hand-verified). The matcher screens the slot's worst V_Ed against it; exceedance flags `review`
  ("bespoke end connection required"), never a gate. Tubes get no capacity opinion.
- **Construction-stage (bare-steel) load case** (CLI `--construction` / `--construction-live`,
  `AreaLoadModel.construction_stage`): every beam slot's envelope gains an erection-stage entry —
  full permanent (wet slab) + the EN 1991-1-6 construction live load (default 0.75 kN/m²) with the
  compression flange **unrestrained**, so χ_LT applies where the persistent case relied on the slab.
  Works on both the analytic and frame paths (isolated-span statics — the diaphragm is not yet
  erected); a beam that passes only via slab restraint is now rejected, not merely warned about.
  Off by default (results unchanged).
- **Full EN 1993-1-1 6.3.3 beam-column interaction, biaxial** (`core/ec3_checks.py`): the simplified
  linear N+M sum is replaced by equations (6.61)/(6.62) with **Annex B (Method 2)** interaction
  factors (Tables B.1/B.2, susceptible/not-susceptible `k_zy`, RHS `k_zz` variant; all `C_m = 1.0`,
  the conservative Table B.3 upper bound). `MemberDemand` gains `Mz_Ed`; minor-axis bending and the
  no-axial biaxial cross-section sum (cl. 6.2.1(7)) are new checks; the frame path now carries `M_y`
  and `M_z` per combination instead of one worst-axis magnitude (a lateral case bending a column about
  both axes is checked about both). Hand-validated against an IPE300 beam-column chain
  (`tests/test_ec3.py`; governing eq. 6.62 = 0.6607). Default gravity results are unchanged.
- **Connection feasibility screen** (`core/connections.py`, CLI `--connections`): each (donor, slot)
  pair is compared geometrically against the slot's **design section** — wrong shape family or
  > 50 mm deeper → incompatible; markedly shallower / thinner web / narrower flange → review. Every
  assignment is annotated (new report "Connection" column, `Assignment.connection_status`); enabling
  the screen excludes incompatible pairs before matching. Geometry only — connection *design* remains
  out of scope; tolerances adjustable via `ConnectionPolicy`.
- **Rect/square HSS support** (388 AISC shapes, `data/sections/us_hss.csv`, verbatim imperial from the
  AISC Shapes Database v15.0): the checker is now **shape-aware** — hollow sections classify every wall
  as an internal part (`c = h − 3t`), use the cold-formed buckling curve c on both axes and the RHS
  shear area `A_v = A·h/(b+h)`, and skip LTB (closed sections are not susceptible). The avoided-new
  baseline is additionally restricted to the slot's **shape family**, so open-section results are
  unchanged by the new tube rows. Catalog now 711 sections (40 EU + 283 US W + 388 US HSS). Round
  HSS/pipe and channels/angles remain out of scope.
- **Geometry confirmation of section names** (`core/sections.py`): the extractors now capture each
  member's measured section dimensions (`h_mm`/`b_mm`/`tf_mm`/`tw_mm` — pyRevit from the type's
  structural-section parameters, IFC from `IfcIShapeProfileDef`), and `resolve_members` uses them to
  confirm a **fuzzy** or **unknown** type name against the catalog by physical dimensions (new mapping
  method `geometry`, confidence 1.0). Unique match required (tolerance `max(1 mm, 1.5%)` per
  dimension); a fuzzy name needs h+b, an unknown name all four. Replaces most manual override-CSV
  confirmation; models without dimensions behave exactly as before.
- **Pre-demolition audit layer** (`core/audit.py`, `--pda`): donor members carry a surveyed condition
  grade (A–D) and verification basis (mill cert / coupon test / documented / visual / unverified),
  supplied in the model JSON or merged from a CSV. These derive a **per-member f_y knockdown**
  (condition × verification, or an explicit value) and **quarantine** unverified or unsuitable
  (condition D) stock from the certified supply, the same way a fuzzy section match is withheld. Adds a
  `recoverable_length_mm` (usable stock after de-construction). Honest by default: a member with no
  audit data is unchanged (admitted at the run default). Provenance surfaces in the material passport,
  the HTML report (audit section + per-assignment Provenance column), and the console. New flags
  `--pda <csv>` and `--include-unverified`; new schema fields on `ExtractedMember`. See
  `docs/PRE_DEMOLITION_AUDIT.md`.

## [0.2.0] - 2026-06-09

First public, release-engineered version. The deterministic EN 1993-1-1 core is unchanged; this
release locks in the previously-uncommitted analysis work and turns the project into a distributable
tool (CI, license, releases).

### Added
- **Global frame analysis** (`--frame-analysis`, `core/frame.py`): the demand model is assembled into
  one connected simple-braced frame (pinned beams, continuous columns, fixed bases) and solved in
  PyNiteFEA, so column axials come from the real load path. Includes EN 1993-1-1 §5.3.2 sway
  imperfection as equivalent horizontal forces + P-Δ (`--phi` / `--pdelta`), wind storey forces
  (`--wind`, EN 1991-1-4 input), the EN 1998-1 lateral-force seismic method (`--seismic`), and
  multi-span beam splitting at interior supports. Per-member analytic fallback where geometry is missing.
- **Load-combination envelope**: members are verified against every ULS combination; the governing
  (worst-utilisation) case is reported, and reuse plus the avoided-new baseline must pass all of them.
- **Optional cutting-stock** mode (`--cut`): one donor cut into several pieces for several slots.
- **MIT `LICENSE`** file (the project already declared MIT in metadata).
- **CI** (GitHub Actions, Windows runner, Python 3.11 + 3.12: ruff + pytest) and a tag-driven
  **release** workflow that builds the wheel/sdist.
- Comprehensive technical documentation (`docs/OVERVIEW.md`).
- `steelreuse --demo` / `--version`; graceful CLI error handling (`--debug` for tracebacks) and
  input validation at the boundary (`ExtractionError`).
- `steelreuse-validate` (a.k.a. `python -m steelreuse.validate_extraction`): sanity-check an
  extraction's member count/sections/coordinates against an expected count or a Revit schedule CSV.
- `docs/VALIDATION.md` (hand-calc / section-table validation, guarded by `tests/test_validation.py`),
  `docs/CASE_STUDY.md` (a real ~1000-member building run), and `docs/UNBLOCK_UV.md`.

### Changed
- Avoided-new baseline is now **standard-aware** (EU vs US): the lightest-adequate new section is
  searched within the slot's own standard.
- EU section catalog expanded to the common range (HEA/HEB/HEM 200–400, IPE160–600).
- Heavy sections (`t_f > 40 mm`): correct EN 1993-1-1 Table 3.1 `f_y` bands and Table 6.2 buckling
  curves.
- χ_LT is surfaced in the default report (the "if unrestrained" value is flagged on slab-restrained
  beams).
- Catalog/carbon CSVs live inside the package (`src/steelreuse/data/`) so an installed wheel finds them.
- Project version is now sourced dynamically from `steelreuse.__version__` (single source of truth).

### Notes
- ML modules remain an **exploratory side-study**, not wired into the certified path.
- The pyRevit extractor has been run against real Revit models (`pyrevit_extension/donortest3.json` /
  `demandtest3.json`, 74/74 and 54/54 columns with plan coordinates); see
  [docs/CASE_STUDY.md](docs/CASE_STUDY.md).

## [0.1.0]

Initial internal version: pyRevit/IFC extractors, EN 1993-1-1 member checks, PyNite force backend,
carbon passport, MILP matching, Jinja2 HTML report with a provider-agnostic LLM narrative, CLI, and a
Streamlit app.

[Unreleased]: https://github.com/cucuradu/circular-steel-reuse/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/cucuradu/circular-steel-reuse/releases/tag/v0.2.0
