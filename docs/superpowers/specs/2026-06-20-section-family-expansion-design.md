# Section-family expansion — CHS, channels, angles

**Date:** 2026-06-20
**Status:** implemented (CHS + channels + angles). EU small-fill deferred — see below.
**Scope:** Tier 1 + Tier 2 of the roadmap section-family expansion, minus the EU small-size fill.

## Goal

Extend the prismatic-member EN 1993-1-1 checker beyond I/H and rectangular hollow to:

- **CHS** (round hollow): US AISC Pipe + round HSS, EN 10210 hot-finished CHS.
- **Channels**: EU UPN (UK PFC / US C/MC deferred).
- **Angles** (axial-dominated reuse): EN + US L, equal and unequal leg.

Deferred to later specs: tees (WT/MT/ST), cold-formed Z/C, **K-series open-web joists**
(a fabricated truss, not a section — gets an identify-but-don't-check track, never a fake EC3 number),
and the **EU small-size catalogue fill** (IPE80–140, small HE). The small-fill was dropped from this
change because the new-build baseline is the *lightest adequate* catalogue section, so adding smaller
sizes lowers the avoided-new baseline for light slots and shifts the documented worked-example and
stewardship carbon anchors — a deliberate, separately-validated change, not a pure data drop. See
FUTURE_IMPROVEMENTS (the baseline-philosophy item).

## Feasibility rationale

The engine represents every member as one `SectionProps` row (A, I, W) through
classification → buckling → LTB → 6.3.3. A family is feasible when (a) BIM/IFC carries it
extractably and (b) it fits that prismatic pipeline.

- **CHS** — doubly symmetric, `is_hollow` already true, clean `D/t` classification, no LTB.
- **Channels** — mono-symmetric; A/I/W from catalogue carry the checks; LTB stays approximate-and-warned.
- **Angles** — principal axes rotated; only axial (bracing/tie) reuse is checkable cleanly. Bending → `REVIEW`.

## Changes (as built)

### `core/sections.py`
- `SectionProps.i_min` (principal minor radius of gyration, mm; defaults to `min(iy, iz)`), `is_round`,
  `Av_z` CHS branch (`2A/π`). `shape` gains `CHS`, `C`, `L`.
- `load_catalog_round(path, metric)` for round sections; `load_catalog` reads an optional `i_min_cm`
  column (angles). `load_default_catalog` merges `us_round.csv`, `eu_chs.csv`, `channels.csv`, `angles.csv`.
- Name mapping: 2-token round-HSS pattern + `_chs_designation` (CHS token detector). UPN normalises via
  the existing generic-profile path; AISC `L` covers angles.

### `core/ec3_checks.py`
- `chs_class` (`D/t`, Table 5.2 sheet 3), `channel_class` (single-outstand flange + internal web),
  `angle_class` (3 or 4). `classify` routes by shape. `_buckling_alpha` adds channel→c, angle→b.
- `N_b_Rd_minor` (angle buckling about principal v-axis via `i_min`). `check_member`: angles are
  axial-only — bending demand sets status `REVIEW` with a warning, no capacity number; channel bending
  warns mono-symmetric.

### `match/optimize.py`
- `_shape_family` / `_slot_shape_family`: the new-build baseline search is confined to the slot's shape
  family (open I/H · hollow · channel · angle), so a channel/angle never becomes the avoided-new
  baseline for an I-section slot. Existing I/H and tube results are unchanged.

### `data/sections/`
- `us_round.csv` (AISC v15 Pipe + round HSS, computed from OD/t), `eu_chs.csv` (EN 10210),
  `channels.csv` (UPN), `angles.csv` (EN L with real `i_min`).

## Honesty constraints (held firm)
- Angle bending → `REVIEW`, never a fabricated principal-axis number.
- Mono-symmetric channel LTB stays the doubly-symmetric `I_t/I_w` approximation, explicitly warned.
- Hollow buckling uses the conservative curve c (hot-finished CHS would be curve a — not claimed).
- K-series joists never get a fabricated member-check number.

## Tests
- `tests/test_sections.py` — round/channel/angle load + units, `i_min`, new-family name mapping,
  family-aware catalog consistency.
- `tests/test_ec3.py` — CHS `D/t` + `A_v`, channel class, angle buckling via `i_min`, angle-bending → `REVIEW`.
- `tests/test_hss.py` — updated hollow count (388 rect + 29 round).
