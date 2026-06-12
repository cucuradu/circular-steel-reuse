# Case study — real Revit building models (US / AISC)

This applies the full pipeline to **real models extracted from Revit** with the pyRevit extractor —
not synthetic data. It shows what the tool does on a messy, real-world input and documents the
limitations it surfaces honestly.

The models (`pyrevit_extension/donor_test2.json`, `demand_test2.json`) are US/AISC steel frames:

| Model | Members | Columns | Beams |
|-------|---------|---------|-------|
| Donor (supply, building to deconstruct) | 1016 | 74 | 942 |
| Demand (new design) | 270 | 54 | 216 |

Reproduce (the `…test_4` models are the current canonical extraction — every member carries
coordinates, 74/74 + 54/54 columns included, **and the measured section dimensions** `h/b/t_f/t_w`
captured by the dimension-aware extractor; three independent extractions of the same building —
test2, test3, test_4 — agree on 1016/270 members with identical role splits. The donor count is
additionally **verified against Revit schedules**: 942 Structural Framing + 74 Structural Columns
= 1016, an exact match — the Phase-1 completeness check):

```powershell
steelreuse --donor pyrevit_extension/donor_test_4.json --demand pyrevit_extension/demand_test_4.json --frame-analysis --out reports/case_study.html
```

## Result (default area-load model, steel-only demand, frame analysis on)

```
Forces: frame analysis (PyNite) — 274 nodes, 492 members
Mapping: 435 mapped, 0 fuzzy, 581 unknown of 1016 members
Supply 435 | demand slots 181 | reused 50
CO2e saved by matches: 39,264 kg  (full donor stock potential: 315,486 kg)
Narrative source: deterministic
```

- **Frame analysis engages:** the demand structure is assembled into a **global PyNite frame of 274
  nodes / 492 elements** and solved — the per-member design forces come from the real load path, not
  per-member closed forms. This needs the two-way-floor handling described under *Limitations* below
  (full-height columns split at their floors, girders kept continuous through secondary-beam crossings).
- **Section mapping:** 435 of the 1016 donor members map to the catalog (the **W-shapes** plus this
  model's one **HSS**, now checked with hollow-section rules); **581 are "unknown"** and are *reported,
  never guessed* — overwhelmingly **open-web bar joists** (482), plus concrete members, channels (C/MC)
  and sizeless L-angles, all intentionally out of scope (joists aren't rolled members; mono-symmetric
  shapes need their own checks; see [FUTURE_IMPROVEMENTS.md](../FUTURE_IMPROVEMENTS.md)). 0 fuzzy
  matches — nothing entered the analysis on a guessed identity.
- **Geometry confirmation behaves honestly on real data:** 465 donor members carry all four measured
  dimensions. Every W-shape among them already maps by name; the 31 dimension-carrying members that
  do *not* map are **L-angles and C-channels**, and the unique-match rule correctly refuses to force
  them onto any W-row — zero false confirmations on ~500 dimension-carrying members.
- **Connection screen on a real model:** with the screen in annotate mode, 8 assignments are flagged
  for connection review (standard fin-plate capacity vs the slot's worst shear, plus the geometric
  rules) — surfaced in the report's Connection column without gating any match.
- **Sway classification exposes the missing lateral system:** running with `--phi 0.005` adds the EHF
  sway cases and the EN 5.2.1(4)B check returns **α_cr ≈ 0.2 — strongly sway-sensitive**. That is the
  *correct* finding, not a defect: the steel skeleton is pinned-beam gravity framing, and the real
  building's lateral system (cores/walls/diaphragm) is non-steel and therefore outside the extraction.
  The tool warns to verify global stability by a dedicated analysis instead of silently treating the
  bare skeleton as laterally adequate. (Under the sway cases 46 of 181 slots reuse, ≈ 54.7 t CO₂e —
  four marginal gravity matches correctly drop.)
- **Matching:** the new design resolves to **181 steel slots** (after steel-only filtering, multi-span
  splitting at columns, and merging each continuous girder into a single reused member); **50 are
  filled by reclaimed members** that pass every EN 1993-1-1 load combination. A whole girder is one
  reusable element: the analytic path now reaches the same 181-slot structure by verifying each span
  joint against column geometry (all 42 multi-span members merge — every recorded joint is a joist
  crossing, not a support; the analytic run fills 42 slots / ≈ 14.4 t with its more conservative
  isolated-span statics). The rest are listed as needing new steel.
- **Carbon:** the matched reuse saves **≈ 39.3 t CO₂e** on the honest *avoided-new* basis (the new
  section each slot would otherwise have required), cleanly separated from the **≈ 315 t** total
  embodied carbon held in the whole donor stock (the theoretical ceiling if everything were reused).

## Limitations this run surfaced (honest reporting)

1. **Two-way floor framing — now handled.** This building is a real girder + secondary-beam floor
   system whose raw extraction came out as **three disconnected pieces**. The diagnosis and fix:
   - *Full-height columns.* 25–27 columns are modelled as single elements running the whole building
     height, so the 2nd-floor beams framed into the bare shaft and floated off. The assembler now
     **splits a column at every floor that frames into it** (`split_columns_at_framing`) and folds the
     storey lifts back into one reused column slot.
   - *Girders vs. secondary beams.* The girders' `spans_mm` records **secondary-beam crossings, not
     column supports**. Splitting each span into an independently-pinned member (correct over a column)
     created **vertical mechanisms** at those crossings. The solver now **keeps a beam moment-continuous
     at an interior crossing that has no column**, so the girder supports the secondary; it still pins
     and slots per-span at genuine column supports. Each continuous girder maps to **one** reused member.
   - *Missing roof columns.* The top-storey columns are genuinely **absent from the extraction** (gap =
     one storey). That piece is still carried on supports at its own level rather than by the columns
     below — a real **re-extraction** gap, not something the solver can invent.

   With these, `--frame-analysis` **engages on the full building** (274 nodes / 492 elements solve with
   physical forces) instead of falling back. The multi-storey column accumulation and the
   simply-supported `wL²/8` recovery are validated in [VALIDATION.md](VALIDATION.md) §1 and in
   `tests/test_frame.py`.
2. **Open-web joists dominate the unknowns.** ~57% of donor members fall in the `unknown` bucket, and
   most (482) are K/LH-series bar joists — built-up trusses, not rolled members, so a section catalog
   can never map them. Rect/square **HSS are now catalogued and checked** (388 shapes, hollow-section
   rules); the remaining mappable families are channels (C/MC) and angles, which need mono-symmetric
   checks before they can be admitted.
3. **Member-level pre-feasibility.** As everywhere in this tool: no connection design, no physical
   material verification — decision support, not a code-certified result.

## Takeaway

On a real 1000-member building the tool runs end-to-end in seconds, assembles and solves a global
frame of the two-way floor system, maps the in-scope steel without guessing, finds a structurally-valid
set of reuse matches, books a defensible ≈ 39 t CO₂e saving, and is candid about what it cannot yet do
(non-W shapes, and the missing roof-storey columns that need re-extraction).
