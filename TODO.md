# Author's personal setup checklist

> **Note:** this is the project author's own environment checklist, not user documentation. To *use*
> the tool, see the **Quickstart** in [README.md](README.md); to *develop* it, see **Setup
> (development)** there.

The things **you** (the human) need to do, in order. Code tasks are tracked separately — this is
only the stuff I can't do for you (install software, build Revit models, get API keys, verify
licenses). Check items off as you go.

---

## ✅ Already done for you
- [x] Project scaffolded at `circular-steel-reuse/`
- [x] `uv` + a managed Python 3.12 installed; `.venv` created
- [x] Phase 0 (scaffold + section catalog) and Phase 1 (extractor + schema + section mapping) built
- [x] 88 tests passing, lint clean (was 16 at Phase 0+1)

---

## 1. Get the dev environment working on your machine (5 min)
- [ ] **Open a brand-new terminal** (the installer added `uv` to PATH, but only new shells see it).
- [ ] Verify the toolchain:
  ```powershell
  uv --version
  cd "c:\Users\Radu\OneDrive\Documents\Python\circular-steel-reuse"
  uv run pytest          # expect: 88 passed
  uv run ruff check .    # expect: All checks passed!
  ```
- [ ] (Optional but recommended) Put the project under version control:
  ```powershell
  git init; git add .; git commit -m "Phase 0+1: scaffold, schema, section mapping"
  ```

## 2. Install Revit + pyRevit (once)  ✅ DONE — Revit 2026 + pyRevit 6.4.0
- [x] Autodesk **Revit 2026** installed.
- [x] **pyRevit 6.4.0** installed (the version that supports Revit 2026 / .NET 8). pyRevit tab loads.
- [ ] **Leave the engine on the default IronPython 3.** Do NOT set the CPython engine: pyRevit 6.x's
      CPython 3.12 has a Revit-2026 bug ("input string '3.12.3' was not in a correct format"). Our
      extractor is stdlib-only and runs fine on IronPython 3.
- [ ] **Install the extractor as a toolbar button** (pyRevit 6.x has no generic "Run script" button):
      pyRevit tab → **Settings** → **Custom Extension Directories** → **Add Folder** →
      `c:\Users\Radu\OneDrive\Documents\Python\circular-steel-reuse\pyrevit_extension`
      → **Save Settings** → **pyRevit → Reload**. A **SteelReuse** tab with an **Extract Steel**
      button appears. (Details: `pyrevit_extension/README.md`.)

## 3. Build small sample Revit models (the test data)
You need two tiny models to exercise the tool end-to-end:
- [ ] **Donor model** — a few steel beams + columns (e.g. 6–10 members) using **standard sections**
      (IPE300, IPE360, HEB300, HEA240…) and set their **structural material** to a real grade (S235/
      S275/S355). This represents the building being deconstructed.
- [ ] **New-design model** — a separate small frame (the new project) whose members are the "demand".
      Include at least one beam that spans **several columns** so the continuous-beam split is tested.
- [ ] Keep them simple — a single bay or two is enough for validation.

## 4. Run the extractor and validate (Phase 1 verification)
- [ ] Open the **donor** model in Revit → run `pyrevit_extract.py` → choose **donor** → save `donor.json`.
- [ ] Open the **new-design** model → run it → choose **demand** → save `demand.json`.
- [ ] In Revit, make a **structural schedule** (count of framing + columns) and confirm the JSON has
      the **same member count** (this is the official Phase 1 check).
- [ ] Send me the two JSON files (or drop them in `data/` ) and I'll run the section mapping on them
      and report anything that lands in the `unknown` bucket.

## 5. Tune the section catalog to your reality (as needed)
- [ ] If your models use sections **not** in `src/steelreuse/data/sections/eu_sections.csv`, tell me which ones and
      I'll add their properties. (Currently: IPE160–500, HEA/HEB200–300.)
- [ ] If Revit type names don't auto-map, we'll create a small override CSV (`raw,canonical`) — you
      just paste the offending names and the section they mean.

## 6. Check your SAP2000 access (needed for Phase 2's optional backend — not blocking)
- [ ] Open SAP2000 → **Help → About**. If it does **not** say "Educational" with a tiny model-size
      cap, you have the full version and the **OAPI is included**.
- [ ] (Optional) Find the OAPI proof: a `CSI OAPI Documentation` `.chm` or the API DLL in the SAP2000
      install folder.
- [ ] If the API turns out to be blocked, no problem — we use the free `PyNiteFEA` solver by default;
      SAP2000 is only an optional upgrade.

## 7. LLM access (only needed at Phase 6 — do later)
- [ ] Get a **free Gemini API key** at https://aistudio.google.com/apikey
- [ ] When we reach Phase 6, put it in a `.env` file as `GEMINI_API_KEY=...` (already git-ignored).
- [ ] (Optional, fully offline alternative) Install **Ollama** (https://ollama.com) and pull a model
      like `llama3.1` if you'd rather run the LLM locally with zero API calls.

---

## Decisions still open (we'll settle these as we hit them)
- [ ] **Embodied-carbon data source** for Phase 3 — ICE database (free CSV) vs Ökobaudat vs Climatiq API.
- [ ] **UI** for Phase 6 — Streamlit dashboard vs a static HTML report (or both).
- [ ] Whether to add the **IFC extractor** (IfcOpenShell) so the tool isn't Revit-only (optional, Phase 7).

## What I do next (no action needed from you)
- Phase 2: the EN 1993-1-1 check engine + `PyNiteFEA` force backend, unit-tested against a hand calc.
- See the full plan: `C:\Users\Radu\.claude\plans\i-am-civil-eng-quirky-giraffe.md`
- See progress table: `README.md`
