# Methodology

How the Circular Structural Reuse Matcher turns two extracted models (a **donor** to be deconstructed
and a new **demand** design) into a set of Eurocode-passing reuse assignments and an embodied-carbon
saving. This document maps every engineering step to the code, states each assumption and its
conservatism direction, and records how the deterministic core is validated.

> **Scope honesty.** This is a **member-level pre-feasibility** screening tool. It does **not** design
> connections, does not run a global frame analysis, and its results are **decision-support, not
> code-certified**. Reused steel additionally requires physical verification (coupon testing,
> corrosion/fatigue survey) and connection design by a qualified engineer.

---

## 1. Pipeline overview

```
donor.json / demand.json            (pyRevit or IFC extractor; lengths in mm)
        │
        ▼  core/sections.py      section-name mapping  (exact → override → normalized → fuzzy → unknown)
        ▼  core/loads.py         area load model → per-member Load (EN 1990 ULS)
        ▼  core/forces.py        Load → MemberDemand (N_Ed, M_Ed, V_Ed) per span
        ▼  core/ec3_checks.py    deterministic EN 1993-1-1 member checks  ← source of truth
        ▼  core/carbon.py        mass + embodied-carbon passport (A1–A3, reuse, net saved)
        ▼  match/optimize.py     sparse feasibility mask → MILP (CBC) → assignments
        ▼  llm/report.py         deterministic numbers + (optional) LLM prose → HTML
```

The deterministic checks are the **single source of truth** (CLAUDE.md rule 3). The ML modules
(`ml/`) and the LLM narrative never alter a number; the LLM only writes prose around fixed numeric
tokens and is screened for invented figures.

## 2. Units convention

Catalogue CSVs are in catalogue units (mm, cm², cm³, cm⁴, kg/m for EU; in, in², in³, in⁴, lb/ft for the
AISC import). Everything is normalised to **internal N, mm at the boundary** in `core/sections.py`, so
stress is N/mm² = MPa throughout. Loads enter in kN/m² and m and are converted to N, mm (`1 kN/m ≡
1 N/mm`). See `schema.UNITS`.

## 3. Section catalog and name mapping  (`core/sections.py`)

- **Catalogs.** European IPE/HE (`data/sections/eu_sections.csv`) and the full **283-shape AISC
  W-series** (`us_sections.csv`, stored verbatim in imperial units from the AISC Shapes Database v15.0
  and converted on load so the source numbers stay auditable). `SectionProps.standard` ∈ {`EU`,`US`}.
- **Mapping** (never silently guesses, CLAUDE.md rule 5): `exact → user-override → normalized → fuzzy
  (quarantined) → unknown`. Fuzzy matches (e.g. `IPE300` vs `IPE330`, ratio ≈ 0.83) are **quarantined
  by default** — reported but excluded from analysis until confirmed via an override CSV — because a
  near-miss would otherwise enter the checks with the wrong section properties.
- **Grades.** EN grades (`S235…S460`) and ASTM (`A992`, `A36`, `A500`, …) in `FY_BY_GRADE`. Ungraded
  AISC members get a conservative shape-based default (W→A992 50 ksi, etc.) and the assumption is
  flagged in the member `notes` (`pipeline._fill_default_grades`).
- **Data integrity.** `tests/test_sections.py::test_catalog_property_consistency` recomputes the derived
  properties from the primaries for **every** row and asserts the physical relations hold (see §10), so
  a transcription slip in either CSV fails loudly.

## 4. Load model  (`core/loads.py`, `core/forces.py`)

The default `AreaLoadModel` derives loads from a **floor-area pressure** the way an engineer would for
pre-sizing, with explicit EN 1990 partial factors rather than a single magic number.

| Quantity | Formula | Default |
|---|---|---|
| ULS design pressure | `γ_G·g_k + γ_Q·q_k` | `1.35·3.5 + 1.5·3.0 = 9.225 kN/m²` |
| Characteristic pressure (SLS) | `g_k + q_k` | `6.5 kN/m²` |
| Beam line load | `p_Ed · tributary_width` | trib 3.0 m (or geometry-estimated) |
| Column axial | `p_Ed · tributary_area · floors` | 9 m² × floors |

- **EN 1990 factors** γ_G = 1.35, γ_Q = 1.5 (Eq. 6.10, STR set B); permanent g_k and imposed q_k default
  to a typical office floor (EN 1991-1-1 cat. B). All overridable on the CLI.
- **Beam tributary widths** can be estimated per-beam from the model geometry
  (`estimate_tributary_widths`): half the gap to the nearest parallel framing neighbour each side, with
  an **edge beam taking the whole bay** (conservative, CLAUDE.md rule 4).
- **Column tributary area + floor count** (`estimate_column_loads`): columns are collapsed to plan grid
  points; tributary area = half-bay each side in x and y (edge = half the present bay, i.e. slab edge at
  the column, no overhang); **floor count = the number of columns in the vertical stack at or above the
  member**, so the lowest column carries every floor above it. Enabled with `--trib-from-geometry`;
  per-member fallback to the configured default where geometry is missing.
- **Notional column moment** (opt-in `--col-ecc`): `M_y,Ed = N_Ed · e`, carried into the column demand so
  the N+M interaction engages. Default `e = 0` (pure axial — real frame moments are not modelled).
- **Continuous beams** are split at supports into `spans_mm` upstream (by the extractor); each span is
  checked as simply supported (conservative for both moment envelope and deflection).

**Forces** (`core/forces.py`): the default `AnalyticBackend` uses the closed-form simply-supported
results `M = wL²/8`, `V = wL/2`. An optional `PyNiteBackend` builds and solves the span in PyNiteFEA and
must agree with the analytic backend for a determinate span (enforced by a test). Columns get a single
axial demand over the full length (buckling length = member length).

## 5. EN 1993-1-1 member checks  (`core/ec3_checks.py`)

Constants: `E = 210 000 N/mm²`, `G = 80 769 N/mm²`, `γ_M0 = γ_M1 = 1.0`. Sign convention: `N_Ed`
compression-positive (negative = tension). Material factor `ε = √(235/f_y)` (Table 5.2).

### 5.1 Cross-section classification (Table 5.2)
- Flange outstand `c = (b − t_w − 2r)/2`, ratio `c/t_f` vs limits `9ε / 10ε / 14ε` → class 1/2/3, else 4.
- Web `c = h − 2t_f − 2r`, ratio `c/t_w` vs `33ε/38ε/42ε` (compression) or `72ε/83ε/124ε` (bending).
- Overall class = worst of flange and web. **Combined N+M conservatively uses the compression web
  limits.** Class 4 (slender) → resistance falls back to `W_el` with a warning and member status
  `REVIEW` (effective-section design is out of scope).

### 5.2 Resistances
| Check | Clause | Implementation |
|---|---|---|
| Tension | 6.2.3 (6.6) | `N_t,Rd = A·f_y/γ_M0` |
| Compression (section) | 6.2.4 (6.10) | `N_c,Rd = A·f_y/γ_M0` |
| Bending (major) | 6.2.5 (6.13)/(6.14) | `M_c,Rd = W_pl·f_y` (cl.1–2) or `W_el·f_y` (cl.3) |
| Shear | 6.2.6 (6.18) | `V_c,Rd = A_v·(f_y/√3)/γ_M0`, `A_v = max(A − 2b·t_f + (t_w+2r)·t_f, h_w·t_w)` |

### 5.3 Flexural buckling (6.3.1)
`N_cr = π²E·I/L_cr²`, `L_cr = k·L`, `λ̄ = √(A·f_y/N_cr)`. Reduction `χ = 1/(φ + √(φ² − λ̄²))` with
`φ = 0.5(1 + α(λ̄ − 0.2) + λ̄²)`, `χ = 1` for `λ̄ ≤ 0.2`. Buckling curves (Table 6.2, rolled I, t_f ≤ 40
mm): `h/b > 1.2` → y-axis curve a (α 0.21), z-axis b (0.34); else y→b, z→c (0.49). Compression members
are governed by the **weaker axis** (`min(N_b,Rd,y, N_b,Rd,z)`); `k_y = k_z = 1.0` (pinned, conservative)
unless set.

### 5.4 Lateral-torsional buckling (6.3.2.3, rolled sections)
`I_t = (2·b·t_f³ + (h − 2t_f)·t_w³)/3` (St-Venant, thin-wall) and `I_w = I_z·h_s²/4` with `h_s = h − t_f`
are approximated **from geometry** (no extra catalog columns); both under-predict `M_cr`, so the result
is conservative. `M_cr = C₁·(π²E·I_z/L²)·√(I_w/I_z + L²G·I_t/(π²E·I_z))`, `λ̄_LT = √(W_y·f_y/M_cr)`. With
the rolled-section method `λ̄_LT,0 = 0.4`, `β = 0.75`, `α_LT = 0.34` (h/b ≤ 2, curve b) else `0.49`
(curve c): `χ_LT = 1/(φ_LT + √(φ_LT² − β·λ̄_LT²))`, capped at `1.0` and `1/λ̄_LT²`; `χ_LT = 1` for
`λ̄_LT ≤ 0.4`. **A restrained compression flange (a floor slab) sets `χ_LT = 1`**; an unrestrained beam
in bending is reduced by `χ_LT` and flagged. `C₁ = 1.0` (uniform moment) is the conservative default.

### 5.5 Combined N + M
A **simplified linear, LTB-aware** interaction (conservative relative to the full 6.3.3):
`N_Ed/min(N_b,Rd,y, N_b,Rd,z) + M_y,Ed/M_b,Rd ≤ 1`, where `M_b,Rd = χ_LT·M_c,Rd` so lateral-torsional
buckling cannot be silently ignored in a beam-column. No favourable `k_yy/k_zy` interaction factors are
applied (they would only relax the check).

### 5.6 Deflection (SLS, optional)
Simply-supported UDL `δ = 5·w·L⁴/(384·E·I_y)` against limit `L/250` (default), using the
**characteristic** (unfactored) service load.

### 5.7 Reclaimed-steel knockdown
`knockdown ≤ 1.0` multiplies `f_y` (a condition/uncertainty proxy for reclaimed material) and is always
flagged; default 1.0 (no reduction) assumes the grade is confirmed by testing — set it lower otherwise.

Member status: `FAIL` if governing utilisation > 1, `REVIEW` if class 4, else `OK`.

## 6. Embodied carbon  (`core/carbon.py`)

Factors from `data/carbon/factors.csv` (ICE v3, 2019, UK structural steel): production `A1–A3 = 1.55`
kgCO₂e/kg, reuse process (clean/test/refabricate) `= 0.10` kgCO₂e/kg. The **material passport** reports,
per mapped member, mass, volume, new-build embodied carbon, reuse process carbon, and the net saved.

## 7. The matcher  (`match/optimize.py`)

1. **Sparse feasibility mask.** A (supply, slot) pair is admissible only if the reclaimed member is long
   enough (`length ≥ required + 50 mm` cut tolerance) **and** passes the exact EN check for that slot's
   forces. Most pairs are infeasible and never enter the model — this tames the MILP size.
2. **Avoided-new baseline (per slot).** The honest CO₂ basis is the **lightest catalog section that
   passes the slot's exact check**, restricted to the **slot's own design standard** (a US slot's
   baseline is a W-shape, not a coincidentally-lighter IPE). Using this rather than the donor's mass
   stops a heavy donor in a light slot from over-booking carbon as "saved".
3. **Net CO₂ saved (booked & reported).** `co2_saved = baseline_mass·A1A3 − used_mass·reuse_process −
   connection_refab`. The off-cut term is a **soft preference only** (the remainder returns to stock, it
   is not emitted) and steers the optimiser but is not booked.
4. **MILP** (PuLP/CBC): binary `x_ij`, each slot ≤ 1 supply, each supply used ≤ 1, maximising
   `Σ score·x` with `score = co2_saved − w_offcut·offcut_mass·saved_per_kg` (`w_offcut = 0.3`,
   `connection_refab = 5 kg` defaults). Only proven-`Optimal` CBC results are trusted; a timeout/error
   escalates to a **greedy fallback** that takes highest-score net-positive pairs first (it never books a
   net-negative match, mirroring the MILP). Reclaimed **supply is not standard-restricted** — reusing a
   donor across standards is legitimate.

## 8. Reporting & the LLM guardrail  (`llm/`)

All figures are computed in Python and injected by Jinja2. If an LLM provider is configured
(Gemini/Ollama), it writes prose only; `find_invented_numbers` rejects any output containing a figure
not present in the computed context and the report falls back to the deterministic narrative. The LLM is
never given a calculator (CLAUDE.md rule 1).

## 9. Assumptions register

| Assumption | Default | Override | Conservatism |
|---|---|---|---|
| Permanent / imposed load | 3.5 / 3.0 kN/m² | `--dead/--live` | neutral (set to project) |
| Partial factors γ_G / γ_Q | 1.35 / 1.5 | `--gamma-g/--gamma-q` | EN 1990 STR |
| Beam tributary width | 3.0 m or geometry | `--trib-width/--trib-from-geometry` | edge = full bay (cons.) |
| Column area / floors | 9 m² / 1 or geometry | `--col-trib-area/--col-floors` | floors=1 **under-loads** lower columns |
| Column moment | 0 (pure axial) | `--col-ecc` | real frame moments not modelled |
| Effective length k | 1.0 | — | pinned (conservative) |
| LTB C₁ | 1.0 (uniform) | — | conservative |
| Compression-flange restraint | restrained (slab) | load model | **non-conservative if slab absent** |
| Reclaimed knockdown | 1.0 | `--knockdown` | assumes grade confirmed |
| Carbon factors | ICE v3 | `factors.csv` | swap for Ökobaudat/Climatiq |

## 10. Validation

**Deterministic core, hand-verified (`tests/test_ec3.py`).** Against IPE300 section tables: `ε(355) =
0.814`; `N_t,Rd(S275) = 1479.5 kN`; `M_pl,Rd = 147.6 kNm (S235) / 172.7 kNm (S275)`; `V_pl,Rd(S235) =
348 kN`; flexural buckling `χ_z(L=4 m, S275) = 0.392`; LTB `χ_LT(L=6 m) ≈ 0.45` and monotone-decreasing
with span; deflection `δ ≈ 9.62 mm (w=10 N/mm, L=6 m)`. Knockdown scales utilisation by `1/k`.

**Matcher (`tests/test_match.py`).** Known-answer feasibility, one-use-each constraints, the avoided-new
basis (a giant donor in a small slot books the baseline, not its own mass), standard-restricted baseline,
degenerate-geometry safety, and the greedy net-positive guard.

**Catalog integrity (`tests/test_sections.py`).** For all 305 rows: `mass ≈ 0.785·A`,
`W_el,y ≈ I_y/(h/2)`, `i = √(I/A)`, `W_el,z ≈ I_z/(b/2)`, `W_pl ≥ W_el` (worst real deviation ≈ 1.5 %).

**Whole suite:** 94 tests, ruff clean.

## 11. Out of scope (explicit non-claims)

Connection design and capacity; global/sway frame analysis and load combinations beyond a single ULS
gravity case; lateral (wind/seismic) and pattern loading; biaxial bending and the shear–moment (6.2.8)
interaction; fatigue, corrosion and weldability of aged steel; effective-section (class 4) design;
cutting one donor into several slots (cutting-stock). See `FUTURE_IMPROVEMENTS.md` for the backlog.
