# pyRevit extension — Steel Reuse extractor

Adds a **SteelReuse** ribbon tab in Revit with an **Extract Steel** button that exports the active
model's structural steel (framing + columns) to JSON for the matcher, plus a **Match** panel that
writes the results back: **Apply Matches** (colours + schedulable reuse parameters),
**Reuse Schedule** (native Revit passport schedule), **Trace Match** (jump from a matched element
to its partner in the other model) and **Clear Matches** (undo).

## Install (one time)

You register the *parent* folder of `SteelReuse.extension` as a pyRevit extension search path:

1. In Revit: **pyRevit tab → Settings**.
2. Scroll to **Custom Extension Directories** → **Add Folder** →
   pick this folder:
   `c:\Users\Radu\OneDrive\Documents\Python\circular-steel-reuse\pyrevit_extension`
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

## Use: Apply Matches

1. Run the matcher with `--apply-matches-out status.json`, e.g.:
   `steelreuse --donor donor.json --demand demand.json --apply-matches-out status.json`
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
   └─ SteelReuse.tab/
      ├─ Extract.panel/
      │  └─ Extract.pushbutton/
      │     ├─ script.py      # runs extractor/pyrevit_extract.py:main()
      │     └─ bundle.yaml    # button title/tooltip
      └─ Match.panel/
         ├─ ApplyMatches.pushbutton/
         │  ├─ script.py      # reads --apply-matches-out JSON: overrides + reuse parameters
         │  └─ bundle.yaml
         ├─ ReuseSchedule.pushbutton/
         │  ├─ script.py      # creates/opens the "SteelReuse Passport" schedule
         │  └─ bundle.yaml
         ├─ TraceMatch.pushbutton/
         │  ├─ script.py      # jump from a matched element to its partner(s) in the other model
         │  └─ bundle.yaml
         └─ ClearMatches.pushbutton/
            ├─ script.py      # undoes Apply Matches (reset overrides, empty reuse parameters)
            └─ bundle.yaml
```
