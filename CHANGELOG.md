# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
  `docs/PRE_DEMOLITION_AUDIT.md`. Closes FUTURE_IMPROVEMENTS #11.

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
  **release** workflow that builds the wheel/sdist and the thesis PDF.
- `THESIS_PRO.md` (canonical thesis) and `build_thesis_pdf.py` (Markdown → HTML → PDF with inline SVG
  figures).
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
