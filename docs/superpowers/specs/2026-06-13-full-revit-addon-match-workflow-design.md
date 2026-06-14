# Full Revit add-on: button-driven match workflow + dockable results panel

**Date:** 2026-06-13
**Status:** Design — awaiting user review (no code written yet)
**Topic:** Turn the SteelReuse Revit tooling into a self-contained add-on so a Revit user can run a
match and explore/filter the results without ever touching a command line.

---

## 1. Problem

Today the end-to-end flow is *almost* a Revit add-on, but it has one command-line step and one
disconnected viewer:

1. Revit (donor model) → **Extract** → `donor.json` *(button — exists)*
2. Revit (new-design model) → **Extract** → `demand.json` *(button — exists)*
3. **Command line** → `steelreuse --donor … --demand … --apply-matches-out status.json --out report.html`
   *(this is the friction — a civil engineer will not touch a terminal)*
4. Revit → **Apply Matches** → pick `status.json` → colours elements + writes Reuse shared-params
   *(button — exists)*
5. Separately → `streamlit run app.py` on localhost, which **re-runs** the pipeline from re-uploaded
   JSONs *(a parallel island — it does not show the run that coloured the model)*

Two problems to solve:

- **The `status.json` step is a terminal command.** Making it a button removes the only non-Revit step.
- **The results viewer is a disconnected localhost app that recomputes** instead of showing the run that
  produced `status.json`, and it offers no in-Revit drill-down (select/zoom the actual element).

## 2. Decisions already made (with the user)

- **Deployment: a full Revit add-on.** No public hosting, no separate localhost dashboard as the primary
  path. Everything becomes part of the SteelReuse tab.
- **View pattern: hybrid, the Revit-native idiom.** *Commands* stay on the ribbon (Extract, Run Match,
  Apply, Trace, Schedule, Clear); the *persistent, filterable results* live in a **dockable panel** — the
  same split Revit itself uses (Properties / Project Browser / System Browser are dockable panels, actions
  are ribbon buttons). The hybrid was chosen over a single "hub" panel because it matches the host, reuses
  the six working buttons, and concentrates all new WPF on the one high-value surface.
- **Engine-agnostic core.** A UI-agnostic orchestration layer plus a versioned `results.json` contract
  sit under the panel, so the WPF panel is the only UI-specific code and a pyRevit HTML output window
  remains a cheap fallback on the *same* contract.

## 3. Goals / non-goals

**Goals**

- A Revit user runs a complete match (Extract → Run → review/filter → Apply) with zero terminal use.
- "Making the `status.json`" becomes a side-effect of clicking **Run Match**.
- An in-Revit, filterable results surface with per-row drill-down (select + zoom the real element).
- The matching engine math is unchanged; this is a UX + integration layer on top of it.

**Non-goals**

- No change to the EN 1993-1-1 checks, the MILP/greedy matcher, frame analysis, or carbon accounting.
- No online hosting in this work. (The Streamlit app stays as a dev / future-online artifact, demoted
  from the primary path but not deleted — it already consumes the same pipeline.)
- No new extraction logic; **Extract** is untouched.
- Not connection design or code-certification; the tool stays member-level pre-feasibility.

## 4. Architecture — four layers

| Layer | Runs as | Responsibility |
|---|---|---|
| **Ribbon commands** | IronPython pushbuttons | Extract *(exists)* · **Run Match** *(new)* · Apply / Trace / Schedule / Clear *(exist)* |
| **Dockable panel** | IronPython + WPF | KPI header, filterable results DataGrid, per-row drill-down |
| **Orchestration** | IronPython-safe module (`runner.py` in the extension) | Locate the signed venv `python.exe`, build CLI args from the Run form, run the subprocess on a background thread, surface progress/errors, hand `results.json` to the panel |
| **Pipeline engine** | signed CPython venv | Unchanged matching engine + **new `--results-out results.json`** export |

The orchestration layer + results contract is the seam: the panel depends only on `results.json`, never on
how it was produced. Swapping the WPF panel for the HTML output window changes nothing below the seam.

### IronPython / environment constraints (carried from the existing extension)

- Pushbuttons + orchestration run on the **default IronPython 3 engine** (pyRevit 6.x CPython 3.12 errors
  under Revit 2026). So all extension-side code is **IronPython-safe**: stdlib only, no f-strings, no
  `%`-formatting beyond what the existing buttons use.
- The matching engine (PyNite, CBC/MILP, pandas, scipy) needs the **signed CPython venv**
  (`...\Python\.venv-signed\Scripts\python.exe`). The extension reaches it via `subprocess`.
- WDAC blocks unsigned binaries, so the orchestration MUST invoke **`python -m steelreuse.cli`**, never the
  pip-generated `steelreuse.exe` launcher (which is blocked).

## 5. Data flow (button-driven, no terminal)

1. **Extract** (donor model) → `donor.json`; **Extract** (new-design model) → `demand.json` *(unchanged)*.
2. **Run Match** → form collects constraints → orchestration runs, on a background thread:
   `python -m steelreuse.cli --donor <donor.json> --demand <demand.json>
   --apply-matches-out <status.json> --out <report.html> --results-out <results.json> [constraints]`
3. On completion → the **dockable panel** auto-loads `results.json` and shows KPIs + the assignments table.
4. **Apply Matches** → consumes `status.json` *(unchanged)* → colours the model + writes shared-params.
5. **Trace / Schedule / Clear** *(unchanged)*.

**Output location:** a single per-run output folder so a run is self-contained — default to a `reports/`
folder beside the active model (configurable). The three artifacts (`status.json`, `report.html`,
`results.json`) share that folder and a run timestamp.

## 6. Component detail

### 6.1 Run Match button (ribbon, IronPython)

- New `RunMatch.pushbutton` in the existing `Match.panel` (placed before Apply Matches).
- Opens a **pyRevit `forms` dialog** collecting the run options (see fields below), then calls the
  orchestration layer. Defaults reproduce the canonical case-study run so a first-time user can just
  press Run.
- Remembers the last-used donor/demand JSON paths and option values (pyRevit script config).

**Form fields** (short by default; advanced behind an expander):

| Group | Field | Maps to CLI |
|---|---|---|
| Inputs | Donor JSON path, Demand JSON path | `--donor`, `--demand` |
| Objective | co2 / members / mass | `--objective` |
| Stock | Cutting on/off | `--no-cut` (when off) |
| Stock | Min utilisation | `--min-util` |
| Stock | Max distinct sections | `--max-distinct-sections` |
| Analysis (expander) | Frame analysis on/off; φ; wind; seismic | `--frame-analysis`, `--phi`, `--wind`, `--seismic` |
| Audit (expander) | PDA CSV; admit unverified | `--pda`, `--include-unverified` |
| Loads (expander) | g_k; q_k; default trib width; trib-from-geometry | `--dead`, `--live`, `--trib-width`, `--trib-from-geometry` |

*Open question for review:* which of the advanced groups (analysis / audit / loads) deserve to be in the
first-cut form vs deferred. Default proposal: ship Inputs + Objective + Stock + the Analysis toggle now;
keep the rest behind the expander with sensible defaults.

### 6.2 Orchestration layer (`runner.py`, IronPython-safe)

A single module the Run Match button and (optionally) the panel's "re-run" depend on. Responsibilities:

- **Interpreter discovery:** read the configured signed-venv `python.exe` path; if unset or missing,
  prompt the user once to locate it and persist it. Validate it can `import steelreuse`.
- **Arg building:** turn the form's option object into a CLI argument list (the one source of truth for the
  invocation), always including `--apply-matches-out`, `--out`, `--results-out`.
- **Execution:** launch the subprocess on a **.NET background thread**; capture stdout/stderr; expose a
  status (running / done / failed) and the output-folder paths.
- **Completion signal:** on success, hand the `results.json` path to the panel (marshalled to the UI
  thread); on failure, surface stderr in a readable error dialog (the CLI already prints `error: …` and
  supports `--debug` for tracebacks).

This module touches **no Revit API** → it is safe to run off the Revit main thread.

### 6.3 Results contract — `results.json` (new engine export)

A new `--results-out PATH` flag on the CLI serialises a JSON-safe slice of the existing report context
(`build_report_context` already assembles all of this; this is a serialiser, not new analysis):

```
{
  "schema_version": 1,
  "kpis": {
    "slots": <int>, "reused": <int>, "co2_saved_kg": <float>,
    "objective": "co2|members|mass", "proven_optimal": <bool>,
    "mass_reused_kg": <float>
  },
  "assignments": [
    {
      "demand_id": "<element id / slot id>",
      "demand_section": "W18X55",
      "donor_id": "<element id>",
      "donor_section": "W12X26",
      "length_mm": <float>,
      "utilisation": <float 0..1>,
      "governing_combo": "ULS gravity",
      "status": "filled|partially_filled",
      "co2_saved_kg": <float>,
      "connection_review": <bool>,
      "quarantined": <bool>
    }
  ],
  "unfilled": [ { "demand_id": …, "demand_section": …, "reason": … } ],
  "quarantined_donors": [ { "donor_id": …, "donor_section": …, "reason": … } ]
}
```

- **`schema_version`** lets the panel and engine evolve independently; the panel checks it and warns on a
  newer/older file.
- The contract is the **only** coupling between engine and panel. `status.json` (element-keyed) is kept
  unchanged for Apply Matches; `results.json` (assignment-keyed) is the new view model.
- *Open question for review:* whether `--results-out` should be implied whenever `--apply-matches-out` is
  used (so a run always produces the panel's data), or stay an explicit opt-in. Default proposal: the
  orchestration always passes both, and the CLI keeps them independent for scripting.

### 6.4 Dockable results panel (WPF DataGrid, IronPython)

- A Revit **dockable pane** registered at extension startup, refreshed after each run.
- **KPI header:** reused / slots / CO₂ saved / objective / "proven optimal" badge.
- **Columns:** Demand id · Demand section · Donor id · Donor section · Util · Status · CO₂ · Gov. case · Conn.
- **Display filters (filter the view, do not re-run):** status dropdown · section text filter · minimum
  utilisation. Changing *matcher constraints* (objective, cutting, min-util-as-a-rule) is a re-run via the
  Run Match form — display filters and matcher constraints are deliberately distinct.
- **Per-row actions:**
  - **Zoom in Revit** — select + zoom the demand or donor element in the active view. Reuses the existing
    Trace Match select/zoom logic; cross-document follow is out of scope for v1 (the element must be in the
    open model).
  - **Apply** — convenience hook into the existing Apply Matches path for the current model.
- **Refresh:** auto after a run + a manual Refresh button (reload `results.json`).

### 6.5 Threading & Revit thread-affinity (the one genuinely hard part)

- The match subprocess runs on a **.NET background thread**; the panel shows a "running…" state so Revit
  never freezes. On the real building (≈435 donors / 181 slots) a run is several seconds.
- The subprocess touches **no Revit API**, so running it off-thread is safe.
- On completion, results loading marshals back to the **UI thread** to populate the DataGrid.
- Every **document action** (zoom / select / Apply) goes through an `IExternalEventHandler` +
  `ExternalEvent` — Revit's hard thread-affinity rule. Trace Match already performs select+zoom, so that
  handler is reused/extended rather than written fresh.

### 6.6 Config & the two-model workflow

- **Interpreter path** stored in extension config (pyRevit script config or a small JSON in the extension
  folder); first run prompts to locate `python.exe` if unset. Always invoked as `python -m steelreuse.cli`.
- **Two documents:** Extract is per open model; Run Match needs *both* JSONs regardless of which model is
  open; Apply needs the relevant model open. The panel is **document-agnostic for review** and **document-
  aware for Apply** (Apply already asks "is this the donor or demand side").

## 7. Interfaces between units (so each unit is testable in isolation)

- **Form → orchestration:** a plain options object (donor path, demand path, objective, flags…). Pure data;
  no Revit, no WPF. Unit-testable arg-building.
- **Orchestration → engine:** a CLI argument list + the signed-venv interpreter path. The engine is a
  black box invoked by subprocess.
- **Engine → panel:** `results.json` (the versioned contract). The panel parses it into a row view-model
  with no knowledge of the pipeline internals.
- **Panel → Revit:** element ids + an `ExternalEvent` handler for select/zoom/apply.

Each boundary is data-only, so: arg-building is testable without Revit; the `results.json` serialiser is
testable in the CPython suite (golden file from the canonical run); the panel's filtering is testable
against a sample `results.json`.

## 8. Error handling

- **Interpreter missing/invalid:** orchestration prompts to locate it; on repeated failure, a clear dialog
  ("could not find a working steelreuse Python — locate the signed venv python.exe").
- **Subprocess non-zero exit:** show the CLI's `error: …` line; offer a "show details" that re-runs with
  `--debug` to capture the traceback. Never silently swallow.
- **Stale / missing `results.json`:** panel shows "no run yet" empty state; a schema-version mismatch shows
  a warning banner rather than crashing.
- **Apply against the wrong model** (element ids absent): the existing Apply Matches already handles the
  donor/demand-side choice; surface a count of unmatched ids as today.

## 9. Testing strategy

- **CPython (existing suite):** a `test_results_export.py` golden-file test — run the canonical
  `donor_test_4` / `demand_test_4` pipeline, dump `results.json`, assert structure + key KPIs
  (181 slots / 71 reused / 60,609.8 kg / proven optimal) and that every assignment row is internally
  consistent (utilisation ≤ 1, status ∈ enum, ids present).
- **Arg-building:** pure-function tests mapping an options object → expected CLI arg list (incl. the
  WDAC-safe `python -m steelreuse.cli` shape and that `--results-out` is always present).
- **Panel filtering:** load a sample `results.json`, assert the status/section/util filters select the
  expected rows. (View-model logic separated from WPF so it can be tested headless.)
- **Manual Revit acceptance** (documented checklist, since it needs the host): Extract → Run Match (no
  terminal) → panel populates → filter → row Zoom selects the right element → Apply colours the model.

## 10. Risks & how they are bounded

| Risk | Bound |
|---|---|
| WPF DataGrid binding under IronPython is fiddly | View-model is plain data + the panel is the only WPF code; HTML output window is a fallback on the same `results.json` |
| Revit freeze during the run | Subprocess on a background thread; document actions via `ExternalEvent` only |
| Dockable-pane registration API (startup hook / `IDockablePaneProvider`) needs verification in pyRevit 6.4 under Revit 2026 | Spike this first (see phases); fallback is launching the results in a pyRevit HTML output window, which needs no pane registration |
| Interpreter path is machine-specific | Config + first-run locate prompt; validated by a trial `import steelreuse` |

## 11. Suggested implementation phases (for the later plan)

1. **Engine seam first:** add `--results-out` + the `results.json` serialiser + golden test (pure CPython,
   no Revit). This unblocks everything and is independently verifiable.
2. **Orchestration layer:** `runner.py` (interpreter discovery, arg-building, background subprocess) +
   arg-building tests. No UI yet — exercisable from a throwaway button that just runs and logs paths.
3. **Run Match button + form:** wire the form to orchestration; prove a terminal-free run end-to-end that
   produces all three artifacts and that Apply Matches still consumes `status.json`.
4. **Dockable panel spike:** confirm pane registration works in this pyRevit/Revit; if it fights, fall back
   to the HTML output window (same `results.json`).
5. **Panel UI:** KPI header, DataGrid, display filters, per-row Zoom (reuse Trace) + Apply.
6. **Polish:** progress/running state, error dialogs, config persistence, docs (README "no-terminal" flow,
   METHODOLOGY note that the engine is unchanged).

## 12. Open questions for the reviewer

1. Run form scope — ship Inputs + Objective + Stock + Analysis-toggle now, rest behind the expander? (§6.1)
2. Should `--results-out` be implied by `--apply-matches-out`, or stay independent? (§6.3)
3. Output folder default — `reports/` beside the active model, or a fixed configured location? (§5)
4. Is cross-document row-zoom wanted in v1, or is "element in the open model only" acceptable? (§6.4)
