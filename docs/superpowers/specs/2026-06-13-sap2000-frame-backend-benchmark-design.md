# SAP2000 frame backend + cross-software benchmark — design

**Date:** 2026-06-13
**Status:** approved (brainstorming) — ready for implementation plan
**Backlog item:** FUTURE_IMPROVEMENTS I-9 *Cross-software benchmark* + `TODO.md §4` (SAP2000 OAPI
high-fidelity force backend) + the `sap2000` extra already declared in `pyproject.toml`.

## Motivation

A SAP2000 free trial (full version, time-limited ~30 days; OAPI only works on the full, **not**
"Educational", build — confirm via Help → About) unlocks two things the project already scaffolds:

1. **External validation authority.** The frame solver (`core/frame.py`, PyNiteFEA) and the EN-force
   chain are validated only against the project's own hand-calcs (METHODOLOGY §8 residual). Solving the
   *same* validated frame in an independent commercial solver (SAP2000) and tabulating the force
   agreement is the external cross-check examiners reliably ask for — the headline thesis §11 artifact.
2. **The optional high-fidelity backend** the docs anticipate (`core/forces.py` docstring: "SAP2000
   backends … plug in behind the same `ForceBackend` protocol later"; CLAUDE.md "Force backend").

User decisions captured during brainstorming (2026-06-13):
- **Keep it experimental** → optional backend in `core/`, **OFF by default**, tests skip when SAP2000
  is absent. Certified results stay byte-identical.
- **Scope: gravity-only, clean frames** → reproduce just the default ULS gravity combination
  (`γ_G·DL + γ_Q·LL`) on connectable frames. Lateral/2nd-order (sway EHF, wind, seismic, P-Δ, α_cr)
  are deferred; the backend **refuses** (returns `ok=False` with a warning) rather than silently
  ignoring such a request.
- **Deliverable: committed table + skipped parity test.**

## Hard constraints (from CLAUDE.md)

- Tests must **not** require SAP2000 (or Revit). The parity test skips cleanly when the OAPI is absent.
- Heavy/COM imports are isolated: only the two new SAP2000 modules import `comtypes`; nothing on the
  default code path does.
- The deterministic EN 1993 check remains the source of truth; this work changes only **where the
  member forces come from**, producing the *same* `FrameResult`/`MemberDemand` objects.
- Default solver stays `pynite`/`analytic`; the new path is reachable only via an explicit flag.

## Key design insight

`core/frame.py` already separates **pure-Python topology** from the **PyNite solve**:
`split_columns_at_framing` → `_expand_spans_tracked` → `snap_nodes` → `_stabilize_topology` produce a
solver-agnostic node/member graph (with the pin-beams / continuous-column / fixed-base decisions).
The SAP2000 path **reuses that exact same topology** and only swaps the solver. Consequences:

- The benchmark is true apples-to-apples: identical nodes, members, releases, supports and loads, so
  any force difference is solver numerics, not modelling choices.
- The new code is small — it is a model *translator* + force *extractor*, not a second frame builder.

## Architecture — 4 new files + 1 small pipeline edit

### 1. `core/_sap2000.py` — OAPI connection helper
- Lazily imports `comtypes`; starts/attaches a SAP2000 instance
  (`CSI.SAP2000.API.SapObject` via `cHelper.CreateObject`/`GetObject`).
- Single clean exception type `Sap2000Unavailable` raised for **every** failure mode: comtypes not
  installed, SAP2000 not present/registered, Educational model-size cap exceeded, COM error.
- Context-manager wrapper that initialises a new blank model in **N, mm** units and guarantees the
  application is closed/released afterwards.
- No other module in the package imports `comtypes`.

### 2. `core/frame_sap2000.py` — the backend
- `analyze_frame_sap2000(demand_members, loads, catalog, combos, options) -> FrameResult`
  — **same signature and return type** as `core.frame.analyze_frame` (drop-in).
- Reuses `core.frame` topology helpers (imported, not duplicated) for the node/member graph.
- Translates that graph into SAP2000 via the OAPI: joints from nodes; frame objects from members;
  restraints (fixed bases) on base nodes; end releases (pin beam ends / keep columns & interior
  beam joins continuous) mirroring `analyze_frame`'s rules; a generic stiff section (determinate
  span forces are section-independent, matching the PyNite backend's `add_section`); `DL`/`LL` load
  patterns; beam UDLs from the `AreaLoadModel`; the gravity ULS load combination.
- Runs the analysis, reads `Results.FrameForce` per member, maps SAP2000 conventions → EN sign
  convention (see Risks), takes the governing N/M/V, builds `MemberDemand` and assembles a
  `FrameResult` with `demands_by_member` / `slots_by_member` exactly like `analyze_frame`.
- **Scope guard:** if `options` requests sway (`notional_phi`), wind, seismic, or `second_order`,
  return `FrameResult(..., ok=False, warnings=["sway/wind/seismic not supported in the SAP2000
  backend yet"])` so the caller falls back — never a silent wrong answer.
- Any `Sap2000Unavailable` or solver failure → `FrameResult(ok=False, warnings=[...])`, identical to
  the PyNite `ImportError` fallback in `analyze_frame`.

### 3. `benchmark/sap2000_bench.py` + console entry `steelreuse-bench-sap2000`
- Builds the **canonical 2-bay frame** reused from the validated `tests/test_frame.py` geometry
  (the layout behind `test_column_axial_accumulates_through_storeys` /
  `test_continuous_beam_loads_the_interior_column`, both anchored to `wL²/8`).
- Runs **analytic + PyNite + SAP2000** over the same frame and load.
- Emits, under `docs/benchmark/` (configurable `--out`):
  - `forces_compare.csv` — per member: `N/M/V` from each solver + `%diff` vs PyNite.
  - `forces_compare.md` — the same as a thesis-ready markdown table.
- Run once on the trial machine; the outputs are committed as the static thesis §11 artifact.
- A SAP2000-absent run fails **loudly** here (the artifact cannot be produced without it) — distinct
  from the pipeline, where absence is a silent fallback.

### 4. `tests/test_sap2000_parity.py`
- Skips (via `pytest.skip` on `Sap2000Unavailable`) when the OAPI is unreachable → CI stays green.
- When SAP2000 is present: builds the same canonical frame, asserts PyNite ≈ SAP2000 per-member
  N/M/V within a tolerance (target ≤ 2 %).
- A separate **non-SAP** unit test covers the pure table-assembly / `%diff` helper (runs in CI).

### 5. Pipeline edit (minimal)
- One optional `--solver pynite|sap2000` flag (default `pynite`) that routes the frame solve to
  `analyze_frame_sap2000`. Default path unchanged → byte-identical certified results.

## Data flow

```
demand_members
  └─ core.frame topology helpers (PURE PYTHON, shared)
       split_columns_at_framing → _expand_spans_tracked → snap_nodes → _stabilize_topology
          │                                  │
          ▼ (PyNite)                          ▼ (SAP2000, NEW)
   analyze_frame                        analyze_frame_sap2000
          │                                  │
          ▼                                  ▼
     FrameResult                        FrameResult        ── same type ──►  matcher / benchmark table
```

## Error handling

| Failure | Behaviour |
|---|---|
| `comtypes` not installed / SAP2000 not registered | `Sap2000Unavailable` → pipeline fallback `ok=False`; benchmark + test report/skip |
| Educational model-size cap hit | `Sap2000Unavailable` (treated as "not the full version") |
| Sway/wind/seismic/2nd-order requested | `FrameResult(ok=False)` with explicit warning (scope guard) |
| Solver instability / non-physical forces | `ok=False` fallback, mirroring `analyze_frame` |
| COM left open on exception | context manager always releases the app |

## Risks

- **Sign/axis convention mapping (primary risk).** SAP2000 `Results.FrameForce` uses its own
  local-axis + sign conventions (axial tension-positive; local 2/3 axes; frame local-axis-1
  orientation), differing from PyNite's (compression-positive; the `-Z`→local-`My`/`Fz` mapping
  documented in `frame.py`). Mitigation: the benchmark **is** the validation — a correct mapping
  reproduces the hand-checked `wL²/8` frame in both solvers; a mismatch is the signal to fix the
  mapping, not a real discrepancy. The mapping is documented explicitly in `frame_sap2000.py`.
- **Trial clock (~30 days).** Scope is deliberately gravity-only so the trial time buys the thesis
  artifact, not full-fidelity parity work. The committed CSV/MD outlive the trial; the parity test
  simply skips once the trial ends.
- **OAPI API-version drift.** Pin nothing in CI (SAP2000 never runs there); document the SAP2000
  version used for the committed benchmark in `forces_compare.md`.

## Out of scope (explicit)

Lateral/2nd-order load cases in SAP2000, the single-span `ForceBackend.beam_span_forces` SAP variant
(no benchmark value — `wL²/8` is exact), the real ~1000-member case-study solve, IFC coordinates,
and any change to the default solver or certified numbers.

## Acceptance

1. `analyze_frame_sap2000` returns a `FrameResult` for the canonical frame that matches
   `analyze_frame` within tolerance (verified by `test_sap2000_parity.py` when SAP2000 is present).
2. `steelreuse-bench-sap2000` produces `docs/benchmark/forces_compare.{csv,md}` with analytic/PyNite/
   SAP2000 columns and %diff.
3. Full test suite passes **without** SAP2000 installed (parity test skips); default-solver results
   are byte-identical to pre-change.
