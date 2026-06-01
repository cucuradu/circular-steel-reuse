# pyRevit extension — Steel Reuse extractor

Adds a **SteelReuse** ribbon tab in Revit with an **Extract Steel** button that exports the active
model's structural steel (framing + columns) to JSON for the matcher.

## Install (one time)

You register the *parent* folder of `SteelReuse.extension` as a pyRevit extension search path:

1. In Revit: **pyRevit tab → Settings**.
2. Scroll to **Custom Extension Directories** → **Add Folder** →
   pick this folder:
   `c:\Users\Radu\OneDrive\Documents\Python\circular-steel-reuse\pyrevit_extension`
3. **Save Settings**, then **pyRevit → Reload** (or restart Revit).
4. A new **SteelReuse** tab appears with the **Extract Steel** button.

## Use

1. Open the Revit model you want to read.
2. **SteelReuse tab → Extract Steel**.
3. Choose **donor** (reclaimed supply) or **demand** (new design).
4. Pick where to save the JSON (e.g. `donor.json` / `demand.json`).
5. A message reports how many members were extracted.

The button runs `../extractor/pyrevit_extract.py` (single source of truth) under the **CPython 3**
engine — the `#! python3` shebang in both files selects it automatically.

## Folder layout (pyRevit's required nesting)

```
pyrevit_extension/                         <- register THIS as a custom extension directory
└─ SteelReuse.extension/
   └─ SteelReuse.tab/
      └─ Extract.panel/
         └─ Extract.pushbutton/
            ├─ script.py      # runs extractor/pyrevit_extract.py:main()
            └─ bundle.yaml    # button title/tooltip
```
