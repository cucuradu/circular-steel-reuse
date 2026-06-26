# Connection data: fixity extraction + donor connection capture — implementation plan

> Status: **planning** (2026-06-25). No code written yet. This is the agreed design for capturing
> connection/fixity data and wiring it into the frame analysis, the pre-demolition check, and the
> match. Open decisions are listed at the end; resolve them before building the affected stage.

## Why

A real run (`steelreuse_reports/*_test_1`, a two-level + mini-roof storage frame) came back
`alpha_cr = 0.1` — strongly sway-sensitive, "k = 1.0 system lengths NOT justified". Root cause is
**not** a solver bug: the frame builder applies one global idealisation — `pin_beams=True`,
`fixed_base=True` (`src/steelreuse/core/frame.py:62-63`) — so **every** beam-to-column joint is a pin
and lateral load is left to fixed column bases acting as cantilevers. An unbraced frame whose joints
are all pinned has almost no lateral stiffness, so `alpha_cr` collapses. The structure is an unbraced
**moment frame** (storage shed, no bracing by design); it only stands up sideways through *rigid*
beam-column joints the model never captured.

There are **two distinct "connection" concerns**, with different data sources. Keeping them separate
is the whole point of this plan:

| | Track A — demand fixity | Track B — donor connection |
|---|---|---|
| Question | Is this joint moment or pinned? base fixed? | Welded / bolted / riveted? how recoverable? |
| Fixes | the `alpha_cr` lateral-load problem | the pre-demolition recovery model (length, carbon) |
| Source | the **authored demand** analytical model (extractable) | **on-site survey / testing** of the old building (SCI P427) — *not* extractable, except modern donor models that carry structural-connection elements |
| Relates to | existing "Semi-rigid connection sensitivity" + "Real frame moments" items in FUTURE_IMPROVEMENTS | existing "Pre-demolition audit & passport" section |

## Research basis (cited)

- **Deconstruction is reverse-construction, bay-by-bay**, after a soft-strip of non-structural
  elements — [PROGRESS deconstruction protocol for steel-framed buildings](https://www.steelconstruct.com/wp-content/uploads/PROGRESS-D2.1b-Deconstruction-protocol.pdf),
  [Lawson Group soft-strip](https://www.lawsongroup.co.uk/soft-stripping-demolition-precise-deconstruction/).
  Consequence: in **full** deconstruction *all* primary steel is recovered; the order is dictated by
  structure, not by connection ease — so there is **no "easy→hard" recovery-sequence feature** (it
  would be physically meaningless and was explicitly rejected).
- **Grade + connection type are surveyed/tested, not modelled** — [SCI P427](https://steel-sci.com/assets/downloads/steel-reuse-event-8th-october-2019/SCI_P427.pdf):
  group members (≤ 20 t), 100% NDT (hardness + spectrometer) + limited destructive testing; reject
  plastically deformed / fire-exposed / corroded members. So the donor data path is a **survey**, and
  the tool's honest-by-default rule (unsurveyed → no penalty) is the correct stance.
- **On-site PDA platforms already exist** — [Material Index](https://material-index.co.uk/audits),
  [BRE SmartWaste](https://www.bresmartsite.com/how-we-help/pre-demolition-audits/),
  [UKGBC PDA tool](https://ukgbc.org/resources/pre-demolition-audit-tool/). So Track B should
  **import** a survey, not reinvent a field app — and **must not** key entry on Revit element ids
  (untraceable on site); anchor to a physically-locatable identifier (Mark / grid / level).

## Design principles

1. **Per-member cascade, never one global flag.** Each member takes connection/fixity from the
   highest-trust source available: surveyed → modelled connection element → analytical release →
   (optional) geometric heuristic → unknown. Every value is tagged with provenance + confidence,
   matching the tool's existing provenance discipline.
2. **Honest fallback.** A member with no data falls back *individually* to today's behaviour
   (`pin_beams` / `fixed_base`); a half-modelled building gets real data where it exists and the safe
   default elsewhere. No all-or-nothing.
3. **Extraction fills what the model knows; it never fabricates.** Old-building donor connections come
   from survey, not from guessing.
4. **Difficulty never gates availability in full deconstruction.** It only shrinks recoverable length
   (cuts) and nudges the recovery-carbon objective.

## Build sequence

The extractor is **one capability, built once**, that runs in `extract()` regardless of `kind` and
populates **both** JSONs with whatever connection data the model exposes. The two sides then carry
*different kinds* of data because the models do — demand models carry authored **analytical fixity**
(releases/base), modern donor models carry **fabrication type** (structural-connection elements). The
*consumers* differ; the extractor does not. Old-building donors (no model) yield nothing here — that
is Phase 3 by design.

- **Phase 1 — shared extractor + schema (both JSONs).** A1 (extractor reads) + A2 (schema fields),
  reading analytical releases/base *and* structural-connection fabrication type in one pass. Schema is
  built+tested here; the Revit reads are the stub validated live.
- **Phase 2 — the two consumers.** A3 (frame builder → structural check, fixes `alpha_cr`) **and**
  B2 (deconstruction → **value case**: recovery length + carbon). Both fully testable here.
- **Phase 3 — old-building problem.** B1-i survey import (Mark/grid/level, PDA-platform interop) for
  donors with no model.

The Track A / Track B headings below are the *data-kind* view (fixity vs fabrication); the phases above
are the *build-order* view across them.

## Track A — demand fixity (the lateral fix)

- **A1 · Extractor** (`extractor/pyrevit_extract.py`, IronPython-safe, version-guarded `getattr` like
  the existing `.Value`/`.IntegerValue` handling): add `_releases(elem)` reading the **AnalyticalMember**
  end releases (Revit 2023+; older `AnalyticalModel`) and `_base_condition(elem)` reading the column
  foot boundary condition. Emit `moment_i`, `moment_j`, `base_fixed` (+ `*_provenance`), each `None`
  when the model has no analytical data. **Only validatable in live Revit.**
- **A2 · Schema** (`src/steelreuse/schema.py`, `ExtractedMember`): add the optional fields, default
  `None` = "unknown → use the global heuristic".
- **A3 · Frame builder** (`src/steelreuse/core/frame.py:744-755` releases, `:730-733` base): if a member
  carries explicit release/base data, honour it; else fall back to `options.pin_beams` /
  `options.fixed_base`. Keep the existing "release only at real supports" logic as the fallback. Add a
  warning line: "fixity: N/M joints from Revit analytical releases, K defaulted to pinned".
- **A4 · Tests** (pure Python): a moment-frame fixture with explicit `moment_i/j=True` must raise
  `alpha_cr` well above the all-pinned value; a `None` member must reproduce today's result exactly.

## Track A — Source 3: bring your own analysis (engineer analysed elsewhere)

Many engineers run the structural checks in a dedicated FEA package and will **not** re-author the
analytical model in Revit. Revit's IFC export **drops the analytical model** (releases/supports are
lost — [Autodesk](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-export-analytical-model-to-IFC-in-order-to-use-the-file-with-FEM-programs-from-Revit.html)),
so fixity must be ingested **directly from the analysis software**, bypassing Revit. The ingest emits
the **same `ExtractedMember` JSON** (geometry + section + `moment_i/j` + `base_fixed`), so everything
downstream (frame check, value case, match) is unchanged — it is a third extractor front-end alongside
the Revit reader (A1) and the IFC reader (`ifc_extract.py`).

Two mechanisms cover the field:

- **SAF (Structural Analysis Format)** — open, Excel-based; carries nodes, members, sections, supports
  and **hinges/releases** ([SCIA](https://www.scia.net/en/innovations/structural-analysis-format-saf)).
  The vendor-neutral target: one SAF importer covers SCIA, Tekla SD, Dlubal, ArchiCAD.
- **Native / API** — per-vendor model file or API where SAF is absent.

### SAP2000
- Path: the **CSI OAPI** — this repo **already has a SAP2000 backend** (`core/frame_sap2000.py`), so
  reading the engineer's model (members, sections, **restraints + frame releases**, and solved member
  forces) reuses an existing connection. Alternatives: the `.s2k`/`.b2k` text model, or the free
  **CSiXRevit** link into Revit (then the A1 Revit reader picks it up).
- Carries: full analytical model + results. **Cheapest to build — backend exists.**

### ETABS
- Path: the **same CSI OAPI family** as SAP2000 (shared API surface), so the SAP2000 backend pattern
  extends to ETABS with little new code. Alternatives: `.e2k`/`.edb` export, or CSiXRevit.
- Carries: full analytical model + results. Reuses the SAP2000 work.

### Robot Structural Analysis
- Path: the native **Revit ↔ Robot link** (AEC Collection) pushes Robot's analytical model — with
  releases/supports — *into* Revit, where the A1 Revit reader then reads it. Robot also exposes a COM
  API / file export. **Robot does NOT support SAF**, so the Revit-link (or API) is the route.
- Carries: full analytical model (+ results via API).

### SCIA Engineer
- Path: **SAF** (SCIA originated the format — cleanest, native export). Direct SAF ingestion, no Revit.
- Carries: full analytical model + releases via SAF.

### Tekla Structural Designer
- Path: **SAF** export → the same SAF importer as SCIA. (Tekla SD also has its own API.)
- Carries: full analytical model + releases via SAF.

**Build order within Source 3:** SAP2000 first (backend exists) to prove the path → one **SAF importer**
(unlocks SCIA + Tekla SD together) → ETABS (extends CSI) → Robot (folds into A1 via the Revit link).
Each is untestable without the respective software/licence — like the Revit reader, you validate each
against a real export. See also the related FUTURE_IMPROVEMENTS item on **importing the engineer's
computed member forces as the demand** (bypassing the in-tool frame solve entirely).

## Track B — donor connection & deconstructability

- **B1 · Source.** (i) **Survey import**: extend `survey.py` / Import Survey with `connection_type`,
  `deconstructability` columns, keyed on a physically-traceable id (Mark / grid / level), aligned to
  the existing PDA shared params. (ii) **Optional model extract**: when a donor model carries Revit
  **Structural Connection** elements, read them → map to welded/bolted/riveted.
- **B2 · Deconstruction** (`src/steelreuse/core/deconstruction.py`): already consumes
  `connection_type` + `deconstructability` (welded/riveted → cut both ends, length loss, carbon ×1.4/1.5;
  bolted → clean). Add provenance plumbing; confirm the unknown-default policy (open decision below).
- **B3 · Matching** (`src/steelreuse/match/optimize.py`): **full deconstruction only** — difficulty
  never gates availability (every member is recovered). Keep the `effective_recoverable_length` effect
  (cuts → shorter stock → may not fit a slot). Add **recovery carbon as a soft objective term** so the
  optimiser prefers low-effort recovery as a *tiebreaker*, never excluding a member. No full/selective
  toggle (selective is out of scope); `labour.py` stays parked.

## Predicting fixity when it isn't authored (method ranking)

**No method reliably predicts a joint's true *behaviour* from geometry alone** — that is why EN 1993-1-8
requires the connection design and SCI P427 requires survey + testing. Anything "predicted" is a *prior
to confirm*, never a safe analysis input (DESIGN_PRINCIPLES: decision support, never a gate). Two
distinct targets that do **not** map 1:1 — a welded joint is *usually* rigid and a fin-plate *usually*
pinned, but a bolted end-plate can be fully moment, so **fabrication type ≠ fixity**:

- **Fixity** (moment vs pinned) → the structural behaviour; fixes `alpha_cr` (demand-side).
- **Fabrication type** (welded/bolted/riveted) → donor recovery/deconstructability.

| # | Method | Predicts | Reliability | Notes |
|---|--------|----------|-------------|-------|
| 1 | Read authored analytical releases / connection elements (Revit, Tekla, Geometry Gym) | both | ★★★★★ | Reading, not predicting — Track A / B-ii. Only when present. |
| 2 | On-site survey + NDT (SCI P427: hardness+spectrometer+sampling) | both | ★★★★★ | Measurement — the donor ground truth (Phase 3). |
| 3 | EN 1993-1-8 stiffness classification (`Sⱼ,ᵢₙᵢ·Lᵦ/E·Iᵦ` vs rigid ≥ 8 braced / 25 unbraced, pinned ≤ 0.5) | fixity | ★★★★☆ *if detail known* | Classifies a *known* connection; needs the detail (plates/bolts → `Sⱼ,ᵢₙᵢ`). Can't conjure an unknown joint. |
| 4 | **Stability-implied inference** (no lateral system in model + building stands ⟹ moment frame) | fixity (frame-level) | ★★★★☆ | The one strong, **free, computable-now** signal. Tells you the joints can't all be pinned — not *which*. Directly attacks `alpha_cr`. |
| 5 | ML / computer vision on point clouds/photos (CNN/YOLO, ~90-93% bolt detection) | fabrication | ★★★☆☆ | Detects bolt-vs-weld; needs imagery + training data; says nothing about fixity. |
| 6 | Age/era heuristic (riveted ~1850-1950; welded since 1920s; HS bolts from 1950s-60s) | fabrication | ★★★☆☆ | A prior from construction date. Narrows probability, not certainty. |
| 7 | Geometric/topological heuristics (web-vs-flange framing, continuity, member degree) | fixity | ★★☆☆☆ | Suggestive only; flag low-confidence and verify. |
| 8 | Family/type-name & detail parsing ("fin plate", "moment connection") | both | ★★☆☆☆ | Reliable when the modeller named it — usually they didn't. |
| 9 | Back-calculation / model updating (fixity that reproduces measured deflection/vibration) | fixity | ★★☆☆☆ | Needs real measured response; otherwise circular. |

Sources: [SteelConstruction.info — moment connections](https://www.steelconstruction.info/Moment_resisting_connections),
[IDEA StatiCa joint classification](https://www.ideastatica.com/support-center/joint-classification-en),
[Geometry Gym structural detection](https://technical.geometrygym.com/rhino-grasshopper/structuralanalysis/geometry-gym-model/structural-model/converting-analysis-models/structural-analysis-detection),
[deconstruction element detection (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0926580522005672),
[bolted-connection deep learning (Springer)](https://link.springer.com/article/10.1007/s41062-025-01860-y),
[historic riveted steel (Vertex)](https://vertexeng.com/insights/historic-building-systems-series-1/),
[riveted/welded/HS-bolted eras (UpCodes)](https://up.codes/s/riveted-and-welded-and-high-strength-bolted-connections),
[SCI P427](https://www.researchgate.net/publication/339713300_SCI_P427_-_Structural_Steel_Reuse_assessment_testing_and_design_principles).

**Cascade decision (resolves open #4):** read (1) → else **stability-implied flag + moment-frame
toggle (4)** → else honest unknown. Tiers 5-9 are **survey-assist hints only** (tagged, unverified) —
never silent inputs to the EC3 check. A *predicted* fixity feeding a member check manufactures false
safety; geometric inference (tier 7) is therefore **not** built as an analysis driver.

### Sub-feature: stability-implied lateral-system check (tier 4 — worth building now)

The only prediction worth building, because it is reliable, free, and directly fixes the `alpha_cr`
problem without per-joint guessing:

- **Detect** whether the model carries *any* lateral system: explicit bracing role, diagonal members at
  brace angles (~30-60° — the storage-frame diagonals were 2.3° roof rafters, *not* braces), or a
  shear-wall/core proxy.
- **If none and the frame is sway-sensitive** (`alpha_cr` low): the all-pinned default is provably wrong
  — the frame is **either a moment frame or laterally deficient**. Emit a distinct, louder warning than
  today's generic "rerun with --phi", and surface the **global moment-frame assumption toggle**
  (`pin_beams=False`, exposed via CLI/panel) as the engineer's one-switch response.
- **Never** silently flip to moment behaviour — it is a flagged choice, with the re-solved `alpha_cr`
  shown for both assumptions so the engineer sees the bracket.

This pairs with Track A: where the analytical model authored real releases, use them (tier 1); where it
is silent *and* there is no lateral system, raise the stability-implied flag (tier 4) instead of
silently assuming a pinned frame that cannot stand.

## What I can build/test vs what needs you

- **Pure Python (I build + unit-test):** A2 schema, A3 frame builder, A4 tests, B2 deconstruction,
  B3 matching, survey-import parsing (B1-i).
- **Live Revit only (you validate):** A1 release/base extraction, B1-ii structural-connection extraction.
  These are IronPython, untestable outside Revit — like the rest of `pyrevit_extract.py`.

## Decisions (all resolved)

1. **Unknown-connection default** (B2): **honest — no penalty.** A member with no surveyed/extracted
   connection data gets no cut, no carbon uplift (matches `deconstruction.py` today). Optimistic on
   recovery, never invents a cost; the surveyor must confirm before any penalty applies.
2. **Deconstruction mode** (B3): **whole-building (full) only.** Every member is recovered; difficulty
   never gates availability. Selective deconstruction is **out of scope** — no full/selective toggle.
3. **`labour.py`**: **dropped — stays parked, out of scope.** The steel-handling-hours sketch is not
   wired in and will not be; real cost is dominated by non-steel work (asbestos, scaffold, crane). Do
   not surface its hours.
4. **Geometric inference** (see "Predicting fixity when it isn't authored" above): geometric inference
   (tier 7) is *not* built as an analysis driver. Build the **stability-implied check + moment-frame
   toggle** (tier 4) instead; tiers 5-9 stay survey-assist hints only.
