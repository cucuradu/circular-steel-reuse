# Author's personal setup checklist

> **Note:** this is the project author's own environment checklist, not user documentation. To *use*
> the tool, see the **Quickstart** in [README.md](README.md); to *develop* it, see **Setup
> (development)** there.

---

## ✅ Already done

- [x] Project scaffolded, toolchain working (uv + Python 3.12, `.venv`)
- [x] Phases 0–7 built and passing (130 tests, lint clean)
- [x] Revit 2026 + pyRevit 6.4.0 installed; pyRevit tab loads
- [x] Extractor toolbar button installed (`SteelReuse` tab visible)
- [x] Donor + demand models built and extracted (`pyrevit_extension/donor_test2.json`, `demand_test2.json`)
- [x] Pipeline ran end-to-end on real models: 140/349 slots reused, 16 496 kg CO₂e saved
- [x] Gemini API key set up in `.env`; narrative live
- [x] Git repo initialised and pushed to GitHub (`cucuradu/circular-steel-reuse`)

---

## 1. Confirm IronPython engine setting (2 min)

- [ ] **Check the engine is still on IronPython 3** (the default). Do NOT switch to CPython: pyRevit 6.x's
      CPython 3.12 has a Revit-2026 bug ("input string '3.12.3' was not in a correct format"). The
      extractor is stdlib-only and works fine on IronPython 3.

> **Engineering ideas live elsewhere:** the full all-fronts backlog (checks, actions, frame,
> matching, carbon, BIM round-trip, app/UX, software quality, validation, PDA) is in
> [FUTURE_IMPROVEMENTS.md → "Ideas backlog — 2026-06-10 brainstorm"](FUTURE_IMPROVEMENTS.md).
> The items below are the only ones blocked on a human + Revit.

## 2. Re-extract with the dimension-capturing extractor + validate member count

- [x] **Re-run SteelReuse → Extract** on both models — done 2026-06-10: `donor_test_4.json` /
      `demand_test_4.json`, full coordinates (1016/1016, 270/270) **and** measured dimensions
      (465 + 181 members carry all four). Counts agree with test2/test3 (1016 / 270, same role
      splits); frame analysis engages (274 nodes / 492 elements) and reproduces the case-study
      result exactly.
- [x] **Donor verified 2026-06-10:** Revit schedules confirm **942 Structural Framing + 74
      Structural Columns = 1016**, matching `donor_test_4.json` exactly. The Phase-1 completeness
      check is formally passed for the donor.
- [ ] Same two schedules for the **demand** model when convenient (expected: 216 framing +
      54 columns = 270; three independent extractions already agree, so this is the last formality).

## 3. Review the unknown bucket

582 of 1016 donor members landed as `unknown` (concrete columns, bar joists, C-shapes, HSS, L-angles).
This is by design — only W-shapes are in scope for the Eurocode checks. But worth confirming:

- [ ] Open `reports/report_test2_real.html` and scan the unknowns table. If any **W-shapes** appear
      there (unexpected), let me know and I'll fix the mapping.
- [ ] If you want C-shapes or HSS added later, that requires adding shape-aware mono-symmetric/hollow
      checks — note it as a future task rather than fixing now.

## 4. SAP2000 access (optional upgrade — not blocking)

- [ ] Open SAP2000 → **Help → About**. If it does **not** say "Educational" (tiny model-size cap),
      you have the full version and the OAPI is available for the optional high-fidelity force backend.
      Default solver is `PyNiteFEA` (free, built-in) — SAP2000 is only needed for the Phase 7+ upgrade.
