# Author's personal setup checklist

> **Note:** this is the project author's own environment checklist, not user documentation. To *use*
> the tool, see the **Quickstart** in [README.md](README.md); to *develop* it, see **Setup
> (development)** there.

The things **you** (the human) need to do, in order. Code tasks are tracked separately — this is
only the stuff I can't do for you (install software, build Revit models, get API keys, verify
licenses). Check items off as you go.

---

## ✅ Already done

- [x] Project scaffolded, toolchain working (uv + Python 3.12, `.venv`)
- [x] Phases 0–7 built and passing (130 tests, lint clean)
- [x] Revit 2026 + pyRevit 6.4.0 installed; pyRevit tab loads
- [x] Gemini API key set up in `.env`
- [x] Embodied-carbon data source decided (Phase 3 complete)
- [x] UI decided: Streamlit dashboard (`app.py`) + HTML report
- [x] IFC extractor implemented (Phase 7)

---

## 1. Put the project under version control (5 min)

- [ ] Run in a terminal:
  ```powershell
  cd "c:\Users\Radu\OneDrive\Documents\Python\circular-steel-reuse"
  git init; git add .; git commit -m "Phases 0–7: complete pipeline"
  ```

## 2. Finish the Revit / pyRevit setup

- [ ] **Leave the engine on the default IronPython 3.** Do NOT set the CPython engine: pyRevit 6.x's
      CPython 3.12 has a Revit-2026 bug ("input string '3.12.3' was not in a correct format"). Our
      extractor is stdlib-only and runs fine on IronPython 3.
- [ ] **Install the extractor as a toolbar button:**
      pyRevit tab → **Settings** → **Custom Extension Directories** → **Add Folder** →
      `c:\Users\Radu\OneDrive\Documents\Python\circular-steel-reuse\pyrevit_extension`
      → **Save Settings** → **pyRevit → Reload**. A **SteelReuse** tab with an **Extract Steel**
      button should appear. (Details: `pyrevit_extension/README.md`.)

## 3. Build small sample Revit models (the test data)

You need two tiny models to exercise the tool end-to-end:

- [ ] **Donor model** — a few steel beams + columns (e.g. 6–10 members) using **standard sections**
      (IPE300, IPE360, HEB300, HEA240…) and set their **structural material** to a real grade (S235/
      S275/S355). This represents the building being deconstructed.
- [ ] **New-design model** — a separate small frame (the new project) whose members are the "demand".
      Include at least one beam that spans **several columns** so the continuous-beam split is tested.
- [ ] Keep them simple — a single bay or two is enough for validation.

## 4. Run the extractor and validate (Phase 1 real-Revit check)

- [ ] Open the **donor** model in Revit → click **Extract Steel** → choose **donor** → save `donor.json`.
- [ ] Open the **new-design** model → click **Extract Steel** → choose **demand** → save `demand.json`.
- [ ] In Revit, make a **structural schedule** (count of framing + columns) and confirm the JSON has
      the **same member count** (this is the official Phase 1 check).
- [ ] Drop both JSON files in `data/` and run `steelreuse --donor data/donor.json --demand data/demand.json --out reports/report.html`.
      Review the report — anything in the `unknown` bucket needs a section catalog fix (see step 5).

## 5. Tune the section catalog (as needed)

- [ ] If your models use sections **not** in `src/steelreuse/data/sections/eu_sections.csv`, note which
      ones and I'll add their properties (currently: IPE160–500, HEA/HEB200–300, W-shapes).
- [ ] If Revit type names don't auto-map, we'll create a small override CSV (`raw,canonical`) — paste
      the offending names and the section they mean.

## 6. Check your SAP2000 access (optional upgrade — not blocking)

- [ ] Open SAP2000 → **Help → About**. If it does **not** say "Educational" with a tiny model-size
      cap, you have the full version and the **OAPI is included** (used for the optional SAP2000
      backend in Phase 7+).
- [ ] Default solver is `PyNiteFEA` (free, built-in) — SAP2000 is only needed if you want the
      optional high-fidelity backend.
