# Case study — real Revit building models (US / AISC)

This applies the full pipeline to **real models extracted from Revit** with the pyRevit extractor —
not synthetic data. It shows what the tool does on a messy, real-world input and documents the
limitations it surfaces honestly.

The models (`data/case_study/donor.json`, `data/case_study/demand.json`) are US/AISC steel frames:

| Model | Members | Columns | Beams |
|-------|---------|---------|-------|
| Donor (supply, building to deconstruct) | 1016 | 74 | 942 |
| Demand (new design) | 270 | 54 | 216 |

Reproduce (`data/case_study/` holds the canonical extraction — every member carries
coordinates, 74/74 + 54/54 columns included, **and the measured section dimensions** `h/b/t_f/t_w`
captured by the dimension-aware extractor; three independent extractions of the same building agreed
on 1016/270 members with identical role splits before this canonical set was retained. The donor count
is additionally **verified against Revit schedules**: 942 Structural Framing + 74 Structural Columns
= 1016, an exact match — the Phase-1 completeness check):

```powershell
steelreuse --donor data/case_study/donor.json --demand data/case_study/demand.json --frame-analysis --out reports/case_study.html
```

## Result (default area-load model, steel-only demand, frame analysis on)

```
Forces: frame analysis (PyNite) — 274 nodes, 492 members
  [α_cr = 0.2 < 10 — strongly sway-sensitive; k = 1.0 system lengths NOT justified;
   verify global stability by a dedicated analysis]   (always-on sway probe — see below)
Mapping: 435 mapped, 0 fuzzy, 581 unknown of 1016 members
Supply 435 | demand slots 181 | reused 71 (cutting-stock)
Matching: MILP proven optimal (CBC) — best possible net-CO2 assignment under the use constraints
CO2e saved by matches: 59,587 kg  (full donor stock potential: 315,486 kg)
Cut donors: 54 | reusable remainder 167.6 m
Narrative source: deterministic
```

(**Cutting-stock is the default** — reclamation stockists cut members to length routinely, e.g. the
18.8 m W14X109 donors each fill two ~7 m slots. `--no-cut` restricts to whole-member reuse: 50
slots fill and ≈ 38.5 t CO₂e books, the difference being long donors stranded by the one-piece rule.)

- **Frame analysis engages:** the demand structure is assembled into a **global PyNite frame of 274
  nodes / 492 elements** and solved — the per-member design forces come from the real load path, not
  per-member closed forms. This needs the two-way-floor handling described under *Limitations* below
  (full-height columns split at their floors, girders kept continuous through secondary-beam crossings).
- **Section mapping:** 435 of the 1016 donor members map to the catalog (the **W-shapes** plus this
  model's one **HSS**, now checked with hollow-section rules); **581 are "unknown"** and are *reported,
  never guessed* — overwhelmingly **open-web bar joists** (482), plus concrete members, channels (C/MC)
  and sizeless L-angles, all intentionally out of scope (joists aren't rolled members; mono-symmetric
  shapes need their own checks; see [OVERVIEW.md](OVERVIEW.md) §13). 0 fuzzy
  matches — nothing entered the analysis on a guessed identity.
- **Geometry confirmation behaves honestly on real data:** 465 donor members carry all four measured
  dimensions. Every W-shape among them already maps by name; the 31 dimension-carrying members that
  do *not* map are **L-angles and C-channels**, and the unique-match rule correctly refuses to force
  them onto any W-row — zero false confirmations on ~500 dimension-carrying members.
- **Connection screen on a real model:** with the screen in annotate mode, 16 assignments are flagged
  for connection review (standard fin-plate capacity vs the slot's worst shear, plus the geometric
  rules) — surfaced in the report's Connection column without gating any match.
- **Sway classification exposes the missing lateral system:** the EN 5.2.1(4)B check is **always on** —
  an EHF *probe* (the EN base imperfection φ₀ = 1/200) measures the sway stiffness on every frame solve
  (α_cr is a stiffness ratio, independent of the probe magnitude), so the default run already returns
  **α_cr ≈ 0.2 — strongly sway-sensitive** and flags that k = 1.0 system lengths are not justified.
  That is the *correct* finding, not a defect: the steel skeleton is pinned-beam gravity framing, and
  the real building's lateral system (cores/walls/diaphragm) is non-steel and therefore outside the
  extraction. The tool warns to verify global stability by a dedicated analysis instead of silently
  treating the bare skeleton as laterally adequate. Passing `--phi 0.005` promotes the imperfection to
  a design action and engages the P-Delta solve. (Under the sway cases 63 of 181 slots reuse,
  ≈ 69.4 t CO₂e — marginal gravity matches correctly drop while the heavier sway demands raise the
  avoided-new baselines of the surviving reuses.)
- **Matching:** the new design resolves to **181 steel slots** (after steel-only filtering, multi-span
  splitting at columns, and merging each continuous girder into a single reused member); **71 are
  filled by reclaimed members** that pass every EN 1993-1-1 load combination — 54 donors are cut to
  length (e.g. each 18.8 m W14X109 yields two ~7 m column pieces), leaving ≈ 168 m of reusable
  remainder in stock. A whole girder is one
  reusable element: the analytic path reaches the same 181-slot structure by verifying each span
  joint against column geometry (all 42 multi-span members merge — every recorded joint is a joist
  crossing, not a support). The rest are listed as needing new steel: the stock genuinely runs out
  of adequate long pieces (most of the 212 reclaimed W18X55s are under the 7.6 m the design needs).
- **Carbon:** the matched reuse saves **≈ 59.6 t CO₂e** on the honest *avoided-new* basis (the new
  section each slot would otherwise have required), cleanly separated from the **≈ 315 t** total
  embodied carbon held in the whole donor stock (the theoretical ceiling if everything were reused).
  *Restraint is decoupled between the two sides of this figure:* a reclaimed donor's feasibility is
  judged with the conservative default (compression flange **unrestrained** unless restraint is
  asserted), but the avoided-new baseline is the lighter section a competent new design would actually
  buy — a **slab-restrained** floor beam. Were the baseline sized unrestrained too, the same stock
  would book ≈ 81.8 t; crediting it that way lets a conservative feasibility screen *inflate* the
  saving, so the baseline stays on the realistic restrained design.
- **Objective trade-off (`--pareto`):** the same feasible pairs solved under every goal give 71
  reused / 59.6 t (net-CO₂), 71 / 57.7 t (members-reused), and 71 / 47.5 t (but 81.9 t of steel
  placed) under reclaimed-mass. With `--no-cut` the goals diverge (50 / 38.5 t vs 54 / 43.3 t) —
  that gap is the one-piece off-cut stewardship preference at work, not carbon physics.

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
frame of the two-way floor system, maps the in-scope steel without guessing, finds a solver-proven
optimal set of reuse matches (71 of 181 slots, cutting donors to length as a stockist would), books
a defensible ≈ 59.6 t CO₂e saving, and is candid about what it cannot yet do
(non-W shapes, and the missing roof-storey columns that need re-extraction).
