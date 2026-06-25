# Scenario Sweep — plan & engineer-selectable axes

Status: living plan. Captures the decisions from the sweep design discussions so any session can pick
it up. The orchestration core (`steelreuse_sweep`), the planner + board windows, and the first axes
are already shipped (see `CHANGELOG.md`); the rest of this doc is the agreed roadmap.

## 1. End goal

Stop the engineer hand-running the match, tweaking one dial, and eyeballing the diff two-at-a-time.
Instead: **lock the fixed problem (building, loads, donor stock), pick a few dials to vary, run every
combination at once, and rank them on a trade-off board.** The engineer chooses from a ranked
shortlist instead of hunting for the optimum; the machine does the grunt work of trying everything.

## 2. The realism principle (what is and isn't a sweep axis)

Only sweep dials an engineer **legitimately chooses** — reuse policy and accounting basis. Everything
that defines the *physical model* (loads, partial factors, wind/seismic, frame analysis) is computed to
be **realistic** and held fixed for the whole sweep. Sweeping arbitrary load numbers produces
"magic tests" with no engineering meaning. The structural check is a feasibility gate, not a knob: a
(donor, slot) pair either carries the design loads or it doesn't.

The one load-side exception is a deliberate **robustness check** (e.g. imposed load ×1.25 — "does the
match survive a load bump?"), which is a sensitivity study, not an optimisation knob, and is kept
visually separate from the policy axes.

## 3. Engineer-selectable axes

### 3a. Built — exposed (or to expose) in the planner

| Planner label | Engine param | Values (suggested) | What it decides |
|---|---|---|---|
| **Optimise for** | `objective` | CO₂ saved / members reused / mass reused | what "best" means |
| **Carbon credit basis** | `counterfactual` | avoided-new only / − recycling / − re-rolling | what end-of-life we credit against |
| **National Annex** | `national_annex` | EN / DK / FI / … | country q_k (imposed-load) values |
| **Prefer lightest adequate donor** | `w_overspec` | off / on (e.g. 0.3) | steer a strong donor away from a weak slot |
| **Minimum utilisation** | `min_util` | 0.0 / 0.6 | refuse grossly over-spec pairs (keeps long stock for needy slots) |
| **Max section types** | `max_distinct_sections` | none / 8 | anti-Frankenstein fabrication cap |
| **Reclaimed strength factor** | `knockdown` | 0.9 / 0.8 | how conservative on reclaimed strength |

Capping each axis at ~2 values keeps the grid tractable even with many axes enabled. The planner shows
a live run-count and confirms above 60.

### 3b. Fixed "realistic base" — NOT axes

Computed once per sweep and shared by every point:

- **Loads** — dead/live derived from occupancy preset + tributary geometry (auto roof/floor zoning).
- **Wind / seismic** — the realistic calculated values for the site (a base input, never swept).
- **Partial factors** γ_G = 1.35, γ_Q = 1.5 (EN 1990, code-fixed).
- **Imposed-load reduction** ON (EN 1991-1-1 §6.3.1.2 αA/αn).
- **Frame analysis / P-delta** — set to the realistic level for the structure, same for all points.
- **Moment-shape** ON — sharper, valid EN check (C1/Cm from the real moment diagram). *Now the default
  in the Run Match panel and the sweep base; the engine/CLI default stays off so the validated case
  study reproduces byte-identically.*
- **Occupancy / roof-occupancy** — auto-assigned by zone, not chosen as an axis.
- **Cutting** — always ON (cutting reclaimed steel to length is standard practice; see §6).

### 3c. Omitted (agreed)

- **Include-unverified** — don't admit un-audited donors.
- **Reserve** — experimental; portfolio matching is the principled tool.
- **Occupancy / roof-occupancy as axes** — auto-mapped already.
- **Gamma factors** — code-fixed, not a preference.
- **Demand / donor model as an axis** — out of scope.

## 4. Features not yet built (to build)

| Feature | What it answers | Build size | Notes |
|---|---|---|---|
| ~~**Carbon-factor dataset axis**~~ ✅ **shipped** | how much does the carbon result depend on which EPD database I trust? | small | **Done.** Three provenance-stamped sets in `data/carbon/` selectable via `--carbon-dataset` / planner row: `ice_v3` (default, 1.55), `ice_v4` (= Climatiq "Steel - Section", 1.61), `oekobaudat` (German EPD-BFS, 1.74). Sets differ in the A1-A3 production figure (the number databases disagree on); credits/process held common. Dataset is recorded in the evidence package. |
| **Utilisation policy** | even out donor utilisation instead of some at 100% / some at 50% | small–med | see §5 — largely served by `w_overspec`; the principled addition is a max-min-utilisation objective |
| **Splicing** | join two short donors end-to-end to fill a long slot | medium | feasible & code-covered (AISC 360 J1.4, EC3); needs combine-donors feasibility + a splice carbon/cost penalty (see §6) |

**Out of scope (considered, dropped):** transport carbon, a cost/£ objective, inventory-subset
sweeps, and phased (time-based) availability. Revisit only if a specific project demands one.

## 5. Utilisation distribution (the "some at 100%, some at 50%" question)

**Today:** the matcher maximises the chosen objective (CO₂/members/mass). It does **not** balance
utilisation — a donor much stronger than its slot simply lands at low utilisation if that arrangement
maximised the objective. The only levers that shape the distribution are `min_util` (a floor) and
`w_overspec` (prefer the lightest adequate donor → tighter fits → higher, more uniform utilisation).

**Research context:** studies of real steel frames find average beam utilisation ≈ **0.54**, with
**36–46 % of beam mass** unnecessary, largely because designers are reluctant to exceed ~**0.8**
utilisation. Over-design (low utilisation) is the norm and is wasteful — so for reuse, steering toward
**higher, tighter utilisation is well justified**, *up to* the safety margin that reclaimed-steel
uncertainty demands (which `knockdown` already encodes).

**Recommended approach:**
1. **First, turn on `w_overspec`** — it is precisely the "don't spend the solid column on a thin slot"
   lever and already exists; it raises low utilisations toward a tighter fit.
2. **If that's not enough, add a max-min utilisation objective** ("balanced" mode): maximise the
   *minimum* utilisation (or penalise utilisation variance) so no donor is grossly under-used. This is
   the principled way to "even it out."
3. **Do NOT chase 100 % everywhere** — reclaimed steel needs headroom; the knockdown factor is the
   place that margin lives, not an artificially low utilisation target.

So: largely a tuning question answerable with the existing `w_overspec`/`min_util` dials; a "balanced
utilisation" objective is a clean Tier-3 addition if the engineer wants it as a first-class choice.

## 6. Domain notes (answers to recurring questions)

- **Construction case — is it too harsh?** It is **deliberately conservative**, and that's the point.
  The erection-stage check (EN 1991-1-6) loads the bare beam with **full permanent load + construction
  live (q_ca 0.75 kN/m²) with the compression flange UNRESTRAINED** (no slab yet → χ_LT governs). It
  keeps the full `dead_kpa` (incl. finishes) on purpose — conservative for the casting situation. So it
  is *not* a lighter, easier case; it usually governs *harder* than the in-service (slab-restrained)
  check and exists to catch beams that pass in service but would fail mid-erection. Dial it via
  `--construction-live` / set `--dead` to the real erection-stage permanent load if the default is too
  blunt. Opt-in, off by default.
- **Load take-down logic (double-checked).** Beam tributary width = half the gap to the nearest
  parallel framing neighbour each side; an edge beam takes the whole bay (conservative); clamped
  1–8 m. Columns are clustered into plan-grid stacks; tributary area = half-bay × half-bay (edge =
  half-bay, no cantilever — the exact tributary for a regular grid); floor count accumulates down the
  stack (lowest column carries every floor above, top carries one). Verified correct.
- **Carbon-factor dataset — is it our hard-coded values?** There *is* a dataset
  (`src/steelreuse/data/carbon/factors.csv`) and the values live in our CSV, but they are **sourced,
  not invented**: ICE v3 for new-steel production (A1–A3), worldsteel/SCI/EN 15804 module-D for the
  recycling credit, and the Cambridge/Allwood line for the (research-grade) re-rolling credit. The CSV
  carries version/source provenance comments and feeds the evidence package. Swapping to
  Ökobaudat/Climatiq is the intended extension (and the basis for the dataset axis in §4).
- **Splicing — feasible in practice?** Yes. Full-strength end-to-end splices (bolted or welded) are
  standard and code-covered (AISC 360 §J1.4, Eurocode 3), used for long-span beams and tall columns,
  and explicitly cited as enabling reuse of leftover segments for the circular economy. Each splice is
  a designed connection with real carbon/cost/inspection, so the engine feature must price it.

## 7. Staged ("funnel") sweep — coarse-to-fine

The way to explore a large space without a thousand-run cartesian product:

1. **Stage 1:** vary 4–5 coarse axes → e.g. ~200 runs → rank, mark the trade-off front.
2. **Refine:** keep only the top survivors (front + top N, e.g. 10–50).
3. **Stage 2:** for *each* survivor, vary *more* axes → a small sub-sweep per survivor; re-rank the
   combined results. Drill further as desired.

You prune on the cheap coarse axes first and spend the deeper exploration only on survivors.

**Implementation sketch (small change to the shipped core):**
- Carry each point's **full options** on its board record (today records hold KPIs + `out_dir`, not the
  config).
- "Refine selected" opens the planner with each selected run as a **fixed base**; Stage 2 calls
  `expand_grid(base, new_axes)` **once per survivor** and concatenates.
- `rank` / `pareto_front` already operate on the combined record set — no change.

## 8. Performance model

- Each point is a lean subprocess (the costly finalist-only add-ons — donor-value, verify, disposition
  — are stripped). Lean + parallel is why 9 points beat one fully-loaded Run Match.
- Wall time ≈ (points ÷ workers) × per-point time; linear, not combinatorial.
- `default_workers()` = CPU − 1; `clamp_workers` caps a manual override at the logical core count (more
  gives no speed-up and risks RAM on smaller machines). On a Ryzen 9900X (12c/24t, 64 GB): ~16 keeps
  the box responsive, ~23 maxes throughput; RAM is never the limit.
- **Future speed-up (cells-once):** build the EC3 feasibility cells once and reuse across the cheap
  axes (objective, min-util, max-sections), so only knockdown/cutting/connection points pay the full
  feasibility cost. Same UI, much faster on big grids.

## 9. Suggested build order

1. **Expose the built Tier-1 axes** in the planner (counterfactual, national-annex, w-overspec + the
   existing min-util / max-sections / knockdown), grouped into collapsible theme sections, plain-English
   labels. *(zero engine work)*
2. **Staged "Refine selected"** flow (§7) — carry opts on records, re-expand survivors.
3. **Carbon-dataset axis** (§4) and **balanced-utilisation** objective (§5) — small engine adds.
4. **Splicing** — the one larger feature still in scope.
5. **Cells-once** core speed-up if big grids become routine.

## References

- Steel reuse / cutting: [SCI P427 – Structural Steel Reuse](https://news-sci.com/new-publication-structural-steel-reuse-p427/),
  [Recycling and reuse – SteelConstruction.info](https://www.steelconstruction.info/Recycling_and_reuse),
  [Reuse of reclaimed steel components (systematic review)](https://www.sciencedirect.com/science/article/pii/S2352012425018727)
- Splicing: [AISC / HSS splices – Steel Tube Institute](https://steeltubeinstitute.org/resources/hss-splices/),
  [Steel beam splice connections](https://peb.steelprogroup.com/steel-structure/components/beam-splice/)
- Utilisation / over-design: [Utilization of structural steel in buildings (Royal Society / Moynihan & Allwood)](https://royalsocietypublishing.org/rspa/article/470/2168/20140170/100293/Utilization-of-structural-steel-in),
  [Regularity and optimisation practice in steel frames (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0921344918300090)
