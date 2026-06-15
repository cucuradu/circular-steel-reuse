# pyRevit extension — Steel Reuse

Adds a **SteelReuse** ribbon tab in Revit. **Extract Steel** exports the active model's structural
steel (framing + columns) to JSON; the **Run Match** window then runs the whole pipeline and shows
the results **without ever touching a command line**. The rest of the **Match** panel writes results
back to the model: **Apply Matches** (colours + schedulable reuse parameters), **Reuse Schedule**
(native Revit passport schedule), **Trace Match** (jump from a matched element to its partner in the
other model) and **Clear Matches** (undo).

## The no-terminal workflow (at a glance)

1. **Extract Steel** on the donor model → `donor.json`; again on the new-design model → `demand.json`.
2. **Run Match** → pick the two JSONs, choose options, **Run** → review KPIs, the assignments grid
   and the result tabs; **Zoom** to any element; **Apply Matches** to colour the model — all in one
   window.
3. **Reuse Schedule** for the native passport schedule; **Trace Match** / **Clear Matches** as needed.

## Install (one time)

You register the *parent* folder of `SteelReuse.extension` as a pyRevit extension search path:

1. In Revit: **pyRevit tab → Settings**.
2. Scroll to **Custom Extension Directories** → **Add Folder** →
   pick this folder (the `pyrevit_extension` directory inside your clone of the repository):
   `<repo>\pyrevit_extension`
3. **Save Settings**, then **pyRevit → Reload** (or restart Revit).
4. A new **SteelReuse** tab appears with the **Extract Steel** button.

## Use: Extract Steel

1. Open the Revit model you want to read.
2. **SteelReuse tab → Extract Steel**.
3. Choose **donor** (reclaimed supply) or **demand** (new design).
4. Pick where to save the JSON (e.g. `donor.json` / `demand.json`).
5. A message reports how many members were extracted.

The button runs `../extractor/pyrevit_extract.py` (single source of truth) under the **default
IronPython 3 engine**. We deliberately do *not* request the CPython engine: pyRevit 6.x's bundled
CPython 3.12 has a version-parsing bug under Revit 2026 ("input string '3.12.3' was not in a correct
format"). The extractor is stdlib-only and IronPython-safe, so it runs fine on the default engine.

## Use: Run Match (the SteelReuse window)

**SteelReuse tab → Run Match** opens a window that runs the whole matcher and shows the results — no
terminal:

1. Pick the **Donor** and **Demand** JSON (one or several demand models → portfolio matching); the
   last pair is remembered. Choose the **Objective** and cutting mode; open **Advanced options** for
   the full engine surface (Policy / Carbon / Loads / Load cases / Frame / Audit & checks). Untouched,
   the defaults reproduce the canonical run.
2. **Run Match.** The heavy engine never runs inside Revit (it shells out to the signed CPython venv
   on a **background thread**, so Revit stays responsive); the full run log streams into the progress
   pane. First run asks once for the venv `python.exe` and remembers it.
3. Review: a **KPI header**, a filterable **Assignments** grid (status / section / min-util), and
   result tabs that appear when their data is present — **Unfilled + diagnosis**, **Pareto**,
   **Disposition**, **Portfolio**, **Audit**, **Warnings**.
4. **Zoom to selected element** (or double-click a row) selects + frames that beam in the open model.
   **Apply Matches to model** colours the open model (pick donor/demand side) — the same as the ribbon
   button below. **Open report / Open folder / Export CSV** are in the footer.

Artifacts land in a `steelreuse_reports/` folder beside the demand model: `status.json`
(Apply Matches), `report.html`, `results.json`.

> The window runs on the default **IronPython 3** engine; the matching engine needs the signed CPython
> venv and is invoked as `python -m steelreuse.cli` (Windows Application Control blocks the
> `steelreuse.exe` launcher). The EN 1993 checks and the matcher are unchanged — the window is a UX
> layer over the same `results.json` contract.

## Use: Apply Matches

You can apply from the **Run Match window** (step 4 above) or from the ribbon button — both run the
same code. From the ribbon:

1. Run a match producing `status.json` (the Run Match window writes it automatically; from a terminal,
   `steelreuse --donor donor.json --demand demand.json --apply-matches-out status.json`).
2. Open the **same** donor model (or the same demand model) the JSON was extracted from — element
   ids must match the open document.
3. **SteelReuse tab → Apply Matches**, pick `status.json`, and choose whether this open model is
   the **donor** or the **demand** side.
4. Elements in the active view get a solid-colour graphic override:
   - donor: green = reused, grey = available (mapped, not selected), red = quarantined (failed
     pre-demolition audit), dark grey = unmapped (section not recognized);
   - demand: green = filled (by reuse), amber = partially filled (multi-span), orange = unfilled
     (needs new steel), no override = non-steel.
5. Each element also gets the **SteelReuse shared parameters** (created and bound to structural
   framing + columns on first run; "Comments" is left alone):
   - **Reuse Status** — the status string above;
   - **Reuse Paired With** — donor side: the slot id(s) it fills; demand side: the donor
     element id(s) that fill it;
   - **Reuse CO2 Saved (kg)** — the avoided-new CO₂e for that pairing;
   - **Reuse Note** — the one-line explanation (e.g. "reused -> slot N1#0 (W16X26), saved 120 kg
     CO2e").
6. The pyRevit output window shows a **run summary** (slots filled by reuse, CO₂e saved, donor
   stock) plus **clickable element links** for the statuses that need a human decision
   (quarantined / partially filled / unfilled) — click a link to select + zoom to that element.

Run **Apply Matches** again on the other model (donor vs. demand) to colour both sides.

Parameter definitions live in `steelreuse_shared_params.txt` in this folder (created on first
use). Keep it — stable GUIDs mean re-runs and other models bind the *same* parameters instead of
creating duplicates.

## Use: Reuse Schedule

After Apply Matches: **SteelReuse tab → Reuse Schedule**. Creates (or re-opens) a multi-category
schedule named **"SteelReuse Passport"**: Family and Type | Reuse Status | Reuse Paired With |
Reuse CO2 Saved (kg) | Reuse Note — filtered to elements that carry a reuse status, sorted by
status, with a grand total on the CO₂ column. The model itself answers "how much did reuse save".

## Use: Trace Match

After Apply Matches (on either side): select a matched element and hit **SteelReuse tab →
Trace Match** — it reads the element's **Reuse Paired With** parameter, finds the partner
element(s) in the open documents (a donor's slot(s) in the new design, or the donor member(s)
filling a demand element), **activates the paired model and selects + zooms** to them. Works in
both directions; with nothing selected it asks you to pick. The paired model must be open in the
same Revit session — otherwise the partner ids are printed so you can open it and re-run.

## Use: Clear Matches

Undoes an Apply Matches run on the open model: **SteelReuse tab → Clear Matches**. It finds the
structural framing/columns with a non-empty **Reuse Status** (or a legacy `SteelReuse:` comment
from older versions), resets their colour override in the **active view**, and empties the reuse
parameters. Other overrides, parameters and comments are untouched; the parameter *bindings* stay,
so the schedule columns survive (they just go blank). Overrides are per-view — run it in the same
view you applied them in.

## Folder layout (pyRevit's required nesting)

```
pyrevit_extension/                         <- register THIS as a custom extension directory
├─ steelreuse_shared_params.txt            <- shared-parameter definitions (created on first apply)
└─ SteelReuse.extension/
   ├─ steelreuse_runner_config.json        <- remembered interpreter + last donor/demand (per machine)
   ├─ lib/                                  <- shared modules on the engine path
   │  ├─ steelreuse_runner.py              # options -> CLI args -> background subprocess
   │  ├─ steelreuse_panel.py               # the Run Match WPFWindow (+ ExternalEvent zoom/apply)
   │  ├─ steelreuse_panel.xaml             # the window layout
   │  ├─ steelreuse_panel_model.py         # parse results.json v2 into grid rows + filters
   │  ├─ steelreuse_apply.py               # shared Apply-Matches (overrides + reuse params)
   │  └─ steelreuse_results_view.py        # HTML results view (fallback / Results button)
   └─ SteelReuse.tab/
      ├─ Extract.panel/
      │  └─ Extract.pushbutton/            # runs extractor/pyrevit_extract.py:main()
      └─ Match.panel/
         ├─ RunMatch.pushbutton/           # opens the SteelReuse window (run + review, no terminal)
         ├─ Results.pushbutton/            # re-open the last results.json in an HTML view
         ├─ ApplyMatches.pushbutton/       # reads status.json: overrides + reuse parameters
         ├─ ReuseSchedule.pushbutton/      # creates/opens the "SteelReuse Passport" schedule
         ├─ TraceMatch.pushbutton/         # jump from a matched element to its partner(s)
         └─ ClearMatches.pushbutton/       # undoes Apply Matches (reset overrides, empty params)
```
