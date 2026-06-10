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

- **Catalogs.** European IPE/HE (`data/sections/eu_sections.csv`), the full **283-shape AISC
  W-series** (`us_sections.csv`) and the **388 rectangular/square AISC HSS** (`us_hss.csv`) — the US
  files stored verbatim in imperial units from the AISC Shapes Database v15.0 and converted on load so
  the source numbers stay auditable (HSS: uniform design wall `t_des = 0.93·t_nom`, the basis of all
  AISC tabulated tube properties). `SectionProps.standard` ∈ {`EU`,`US`}; `SectionProps.is_hollow`
  marks tubes for the shape-aware checks. Round HSS and pipe are excluded (CHS needs a `D/t`
  classification rule).
- **Mapping** (never silently guesses, CLAUDE.md rule 5): `exact → user-override → normalized → fuzzy
  (quarantined) → unknown`. Fuzzy matches (e.g. `IPE300` vs `IPE330`, ratio ≈ 0.83) are **quarantined
  by default** — reported but excluded from analysis until confirmed via an override CSV — because a
  near-miss would otherwise enter the checks with the wrong section properties.
- **Geometry confirmation.** When the extractor captured the member's measured section dimensions
  (`h/b/tf/tw` — pyRevit type parameters, or the IFC `IfcIShapeProfileDef`), a fuzzy or unknown *name*
  can be confirmed against the catalog by **physical dimensions** (`resolve_members`, method
  `geometry`): every captured dimension must match within `max(1 mm, 1.5 %)` and the match must be
  **unique**, else nothing is confirmed. A fuzzy name needs `h+b`; an unknown name needs all four
  dimensions (no name signal → stronger physical evidence required). This is identification, not a
  guess — the tolerance band sits far below the step between adjacent catalog sizes — and it removes
  most of the manual override-CSV confirmation work.
- **Grades.** EN grades (`S235…S460`) and ASTM (`A992`, `A36`, `A500`, …) in `FY_BY_GRADE`. Ungraded
  AISC members get a conservative shape-based default (W→A992 50 ksi, etc.) and the assumption is
  flagged in the member `notes` (`pipeline._fill_default_grades`).
- **Data integrity.** `tests/test_sections.py::test_catalog_property_consistency` recomputes the derived
  properties from the primaries for **every** row and asserts the physical relations hold (see §10), so
  a transcription slip in either CSV fails loudly.

### 3.1 Pre-demolition audit  (`core/audit.py`)
The donor model is the deliverable of a **pre-demolition audit** — the survey that records, per
reclaimed member, its physical **condition** and the **basis on which its grade can be trusted**. The
audit layer converts those two facts into numbers the EN 1993 check already understands: a per-member
`f_y` **knockdown** and a **quarantine** decision (unverified or unsuitable stock is excluded from the
certified supply, exactly like a fuzzy section match). Knockdown = `condition × verification` factor
(e.g. condition `B` 0.95 × `documented` 0.95 = 0.9025), unless an explicit per-member `knockdown`
overrides it; a value below `MIN_KNOCKDOWN = 0.30` quarantines rather than zeroing capacity. Audit data
is optional: a member with **none** behaves exactly as before (admitted at the run default, full
length), so the feature changes no legacy result. Supplied in the model JSON or merged from a CSV
(`--pda`); see [`docs/PRE_DEMOLITION_AUDIT.md`](PRE_DEMOLITION_AUDIT.md) for the factor tables, the CSV
schema, and the regulatory context (SCI P427; EU C&D Waste Protocol; *Diagnostic PEMD*; Level(s); CAM).

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
- **Load-combination envelope** (`combination_loads`): each member is verified against a *list* of ULS
  combinations and the **governing** (worst-utilisation) one is reported; a member — and the avoided-new
  baseline — passes only if it passes *every* combination, the way an engineer checks all design
  situations. The default envelope is the gravity case (`γ_G g_k + γ_Q q_k`, EN 6.10) plus, for columns,
  an opt-in **EN 1993-1-1 §5.3.2 global (sway) imperfection** (`--phi`, e.g. `0.005 = 1/200`) applied as
  a notional column moment `M_y,Ed = N_Ed·φ·L` so the N+M interaction engages. `φ = 0` (default) ⇒
  gravity only, so default results are unchanged. Adding further design situations (uplift `1.0G+1.5Q`,
  wind, seismic) is a matter of appending entries to the envelope. This is a **member-level** envelope,
  not a global frame analysis (see §12).
- **Construction-stage case** (opt-in `--construction`, `_construction_demand` in `pipeline.py`): every
  beam slot gains a **bare-steel erection-stage** entry — full permanent load (the wet slab is on the
  beam; finishes/services conservatively included) + the **EN 1991-1-6** construction live load
  (`--construction-live`, default `q_ca = 0.75 kN/m²`), with the compression flange **unrestrained**:
  the slab that justifies `χ_LT = 1` in the persistent situation does not yet exist, so a beam that
  passes only via slab restraint fails here instead of merely being flagged. The entry uses
  isolated-span statics on **both** the analytic and frame paths (the diaphragm/continuity the frame
  assumes is not yet erected); SLS deflection is not re-checked (temporary situation). Columns are
  unchanged (their erection-stage load is lower, never governing in this model).
- **Continuous beams** are split at supports into `spans_mm` upstream (by the extractor); each span is
  checked as simply supported (conservative for both moment envelope and deflection).

**Forces** (`core/forces.py`): the default `AnalyticBackend` uses the closed-form simply-supported
results `M = wL²/8`, `V = wL/2`. An optional `PyNiteBackend` builds and solves the span in PyNiteFEA and
must agree with the analytic backend for a determinate span (enforced by a test). Columns get a single
axial demand over the full length (buckling length = member length).

### 4.1 Global frame analysis  (`core/frame.py`, opt-in `--frame-analysis`)

Instead of synthesising each member's forces in isolation, the whole demand structure can be assembled
into **one connected model and solved** (PyNiteFEA), so the action effects that feed the EN 1993 check
come from a real analysis. The output is the *same* `MemberDemand` per member/combination consumed by
the matcher — only the **source** of the forces changes — so the analytic path remains the always-available
default and fallback.

- **Topology** (`snap_nodes`, pure Python): member endpoints (`start_xyz`/`end_xyz`) within a tolerance
  (50 mm default) are snapped into shared nodes so beams and columns connect. Members without usable
  coordinates are reported and **fall back to the per-member analytic load** (a robust hybrid for messy
  real models); if no connectable geometry exists at all, the whole run falls back.
- **Idealisation — simple braced frame** (the project default): a beam-to-column connection releases the
  **major-axis bending moment** at a *real support* — the beam's true ends and any interior point that
  sits on a column (beams stay simply-supported there, recovering `wL²/8`) — while retaining minor-axis
  and torsional continuity (a realistic shear connection that also gives beam-to-beam joints rotational
  stiffness about the vertical axis, avoiding spurious singularities on real BIM). Columns are
  **continuous** and column bases are **fixed** so the lateral load (§4.1 sway case) is carried by the
  column bases (no explicit bracing) or by the **braces** (pin-ended axial).
- **Two-way floor framing** (`split_columns_at_framing` / continuity-at-crossings): real BIM models a
  girder + secondary-beam floor with full-height columns and continuous girders. The assembler **splits
  a full-height column at every floor that frames into it** (folding the storey lifts back into one
  reused column) and **keeps a girder moment-continuous where a secondary beam crosses it with no
  column below** (so the girder supports the secondary instead of forming a vertical mechanism). A
  continuous girder maps to a single reused member; a beam over interior columns still slots per span.
- **Robustness on messy real models** (`snap_nodes` / `_stabilize_topology`): each **disconnected
  component is supported at its own lowest level** (so a multi-piece or split-level model doesn't leave
  higher pieces floating); members that **hang off the structure** (a free, unsupported end) are pruned
  to the analytic path; and if an irregular model still solves but yields **non-physical forces**
  (an ill-conditioned near-mechanism), a magnitude guard rejects the result and falls back to the
  per-member analytic loads. The frame solve therefore either produces sane forces or falls back — it
  never feeds garbage to the checker (CLAUDE.md rule 4).
- **Loads & load path**: the floor pressure (§4) is applied as a UDL on the **beams only**, split into
  permanent (`DL`) and imposed (`LL`) load *cases*; the ULS/SLS *combinations* apply the EN 1990 factors
  (`γ_G·DL + γ_Q·LL`). **Columns carry no applied load** — each column's axial comes from the solved load
  path, so a multi-storey stack accumulates the floors above it and an interior column correctly collects
  from the beams on both sides. This **supersedes** the `estimate_column_loads` tributary-area/floor-count
  estimate (§4) whenever frame analysis is on.
- **Lateral — global sway imperfection** (`--phi`, EN 1993-1-1 §5.3.2): rather than the member-level
  notional moment of §4, the sway imperfection is applied as **equivalent horizontal forces**
  `H_i = φ·N_Ed` at each column top (computed from the gravity column axials), in each lateral direction,
  giving a real frame lateral case `γ_G·DL + γ_Q·LL + H`. The model is then solved with a **2nd-order
  (P-Δ)** analysis so sway amplification is captured. Each member's force envelope spans gravity + the
  sway cases and the matcher reports the governing one. `φ = 0` (default) ⇒ gravity only. `--pdelta`
  forces the 2nd-order solve without a sway case.
- **Lateral — wind** (`--wind q`, kN/m²): a net façade pressure `q` (the user's EN 1991-1-4 value) becomes
  **horizontal storey forces** `q · width_perp · h_trib` per level — `width_perp` the building plan extent
  perpendicular to the wind, `h_trib` half the storey above + half below — lumped onto each level's column
  tops (rigid-diaphragm). The combination is **wind-leading** (EN 1990 6.10: `γ_G·G + γ_Q·W + γ_Q·ψ₀·Q`,
  `ψ₀ = 0.7` imposed) and carries the sway imperfection where present. Needs a **3-D** model (a planar
  frame has no perpendicular façade → wind is skipped in that direction with a warning).
- **Lateral — seismic** (`--seismic Cs`, EN 1998-1 **lateral force method** §4.3.3.2): the seismic weight
  of each level `W_i = Σ(g_k + ψ₂·q_k)·trib·L` (its beams) gives a base shear `F_b = Cs·ΣW_i`, distributed
  up the height as `F_i = F_b·(W_i·z_i)/Σ(W_j·z_j)` (inverted-triangular first mode) and lumped on each
  level's column tops. `Cs = Sd(T₁)·λ/g` is a **user input** (design spectral acceleration as a fraction of
  g) — the full EN 1998 site/soil/`q` spectrum is out of scope. The seismic situation uses unit factors
  (`G + ψ₂·Q + E`, `ψ₂ = 0.3`, EN 1990 6.4.3.4).
- **Conventions** (verified against PyNite 2.4.1): global `−Z` (downward) load on a horizontal beam gives
  bending as local **My** and shear as local **Fz**; member axial is **compression-positive**, matching
  `MemberDemand.N_Ed`. Section stiffness uses the mapped catalog `A, I_y, I_z` and an open-section St-Venant
  `J ≈ ⅓·Σ b_i t_i³`; unmapped members use a generic stiff section (forces in the determinate parts of a
  simple braced frame don't depend on stiffness).
- **Robustness**: any solver failure (residual instability, missing extra) is caught and the run falls
  back to the analytic loads with a warning, never a crash (CLAUDE.md rule 4).

**Residuals (still open):** lateral cases (sway / wind / seismic) are applied in `+X`/`+Y` (worst-magnitude
for a regular doubly-symmetric frame); the seismic action is the simplified **lateral force method** with a
user base-shear coefficient, not a modal response-spectrum analysis (no torsion/accidental eccentricity);
effective lengths remain `k = 1.0` (the solve gives forces, not buckling lengths). Column **biaxial**
bending is now carried through: the per-combo envelope keeps `M_y` and `M_z` separately and the checker
runs the biaxial 6.3.3 interaction (§5.5); the residual is that member *rotation* about its own axis is
not captured from the BIM, so the local→section axis mapping assumes the default orientation. See
`FUTURE_IMPROVEMENTS.md`.

**Continuous multi-span members** are handled: `expand_spans` splits a beam carrying `spans_mm = [s₁, s₂,…]`
into one sub-element per span at its interior supports (interpolated along the member axis so the interior
nodes land on the columns below), so each span is checked over its own length **and** each bay's reaction
is routed into the correct interior column. The pipeline then makes one slot per span (id `{member}#k`).

## 5. EN 1993-1-1 member checks  (`core/ec3_checks.py`)

Constants: `E = 210 000 N/mm²`, `G = 80 769 N/mm²`, `γ_M0 = γ_M1 = 1.0`. Sign convention: `N_Ed`
compression-positive (negative = tension). Material factor `ε = √(235/f_y)` (Table 5.2).

**Thickness-dependent yield (Table 3.1).** The nominal `f_y` is taken from `nominal_fy(grade, t_f)`:
EN 10025 grades lose strength in thick elements (e.g. S355 → 335 N/mm² for `40 < t ≤ 80 mm`), keyed off
the flange thickness `t_f` (the governing element of a rolled I/H). ASTM grades (A992, A36, …) carry a
single specified minimum `F_y` with no thickness banding. Sections with `t_f > 40 mm` (88 of the AISC
W-shapes) are flagged in the member warnings.

### 5.1 Cross-section classification (Table 5.2)
- Flange outstand `c = (b − t_w − 2r)/2`, ratio `c/t_f` vs limits `9ε / 10ε / 14ε` → class 1/2/3, else 4.
- Web `c = h − 2t_f − 2r`, ratio `c/t_w` vs `33ε/38ε/42ε` (compression) or `72ε/83ε/124ε` (bending).
- **Rect/square hollow (HSS):** every wall is an *internal* part with flat width `c = h − 3t` /
  `b − 3t` (the Table 5.2 RHS convention). The width-side wall is in uniform compression under
  major-axis bending as well as axial load, so it always takes the compression limits; the webs take
  the bending limits in bending. Thin-walled tubes (e.g. HSS12X12X3/16, `c/t ≈ 66`) classify as
  class 4 and get the same `REVIEW` treatment as slender open sections.
- Overall class = worst of flange and web. **Combined N+M conservatively uses the compression web
  limits.** Class 4 (slender) → resistance falls back to `W_el` with a warning and member status
  `REVIEW` (effective-section design is out of scope).

### 5.2 Resistances
| Check | Clause | Implementation |
|---|---|---|
| Tension | 6.2.3 (6.6) | `N_t,Rd = A·f_y/γ_M0` |
| Compression (section) | 6.2.4 (6.10) | `N_c,Rd = A·f_y/γ_M0` |
| Bending (major) | 6.2.5 (6.13)/(6.14) | `M_c,Rd = W_pl·f_y` (cl.1–2) or `W_el·f_y` (cl.3) |
| Shear | 6.2.6 (6.18) | `V_c,Rd = A_v·(f_y/√3)/γ_M0`, `A_v = max(A − 2b·t_f + (t_w+2r)·t_f, h_w·t_w)`; RHS: `A_v = A·h/(b+h)` (6.2.6(3)) |

### 5.3 Flexural buckling (6.3.1)
`N_cr = π²E·I/L_cr²`, `L_cr = k·L`, `λ̄ = √(A·f_y/N_cr)`. Reduction `χ = 1/(φ + √(φ² − λ̄²))` with
`φ = 0.5(1 + α(λ̄ − 0.2) + λ̄²)`, `χ = 1` for `λ̄ ≤ 0.2`. Buckling curves (Table 6.2, rolled I) are
selected from `h/b` **and the flange thickness** `t_f`: for `h/b > 1.2`, `t_f ≤ 40 mm` → y curve a
(α 0.21) / z curve b (0.34), but `40 < t_f ≤ 100 mm` shifts to y curve b / z curve c (0.49); for
`h/b ≤ 1.2` (`t_f ≤ 100`) → y b / z c; and `t_f > 100 mm` → curve d (0.76) both axes. **Hollow sections
use curve c both axes** (cold-formed per Table 6.2 — AISC HSS are A500 cold-formed; hot-finished tube
would rate curve a but we have no fabrication flag, so the conservative curve applies). Compression
members are governed by the **weaker axis** (`min(N_b,Rd,y, N_b,Rd,z)`); `k_y = k_z = 1.0` (pinned,
conservative) unless set.

### 5.4 Lateral-torsional buckling (6.3.2.3, rolled sections)
`I_t = (2·b·t_f³ + (h − 2t_f)·t_w³)/3` (St-Venant, thin-wall) and `I_w = I_z·h_s²/4` with `h_s = h − t_f`
are approximated **from geometry** (no extra catalog columns); both under-predict `M_cr`, so the result
is conservative. `M_cr = C₁·(π²E·I_z/L²)·√(I_w/I_z + L²G·I_t/(π²E·I_z))`, `λ̄_LT = √(W_y·f_y/M_cr)`. With
the rolled-section method `λ̄_LT,0 = 0.4`, `β = 0.75`, `α_LT = 0.34` (h/b ≤ 2, curve b) else `0.49`
(curve c): `χ_LT = 1/(φ_LT + √(φ_LT² − β·λ̄_LT²))`, capped at `1.0` and `1/λ̄_LT²`; `χ_LT = 1` for
`λ̄_LT ≤ 0.4`. **A restrained compression flange (a floor slab) sets `χ_LT = 1`**; an unrestrained beam
in bending is reduced by `χ_LT` and flagged. `C₁ = 1.0` (uniform moment) is the conservative default.
**Hollow sections skip LTB entirely** (`χ_LT = 1`, detail flag `hollow`): a closed section's torsional
stiffness keeps `λ̄_LT` far below the 0.4 plateau for any practical span, and the open-section
`I_t`/`I_w` approximations above would be meaningless for a tube.

### 5.5 Combined N + M — full 6.3.3 (Annex B, Method 2), biaxial
The **full EN 1993-1-1 6.3.3 beam-column interaction**, equations **(6.61)** and **(6.62)**:

```
N_Ed/(χ_y·N_Rk/γ_M1) + k_yy·M_y,Ed/(χ_LT·M_y,Rk/γ_M1) + k_yz·M_z,Ed/(M_z,Rk/γ_M1) ≤ 1   (6.61)
N_Ed/(χ_z·N_Rk/γ_M1) + k_zy·M_y,Ed/(χ_LT·M_y,Rk/γ_M1) + k_zz·M_z,Ed/(M_z,Rk/γ_M1) ≤ 1   (6.62)
```

with the **Annex B (Method 2)** interaction factors (`annex_b_k_factors`): Table B.1 for class 1–2
(I-section and RHS variants of `k_zz`), Table B.2 for class 3 (class 4 is approximated with elastic
moduli and flagged), and the susceptible/not-susceptible `k_zy` split — a restrained flange or a hollow
section is *not susceptible* to torsional deformation (`k_zy = 0.6·k_yy` / `0.8·k_yy`), an unrestrained
open section uses the `C_mLT` form. All **`C_m = 1.0`** (uniform equivalent moment, the Table B.3 upper
bound), so the factors are conservative for any real moment shape. `χ_LT` enters exactly as in the code
equations, so LTB can never be silently ignored in a beam-column. The governing utilization is
`max(6.61, 6.62)`; both values and all four k-factors are reported in the check detail.
**Validated against a hand-computed IPE300 beam-column** (`tests/test_ec3.py`, chain in the test
comments: χ_y = 0.9606, χ_z = 0.3924, k_yy = 1.0358, eq. 6.62 = 0.6607).

Minor-axis bending alone is checked as `M_z,Ed/M_z,Rd` (no LTB about z); biaxial bending **without**
axial uses the always-conservative linear cross-section sum of cl. 6.2.1(7) (the 6.2.9 α/β exponents
would only relax it).

### 5.5a Shear–moment interaction (cl. 6.2.8)
When `V_Ed > 0.5·V_pl,Rd`, bending resistance is reduced with `ρ = (2·V_Ed/V_pl,Rd − 1)²`: rolled I/H
use eq. (6.30) (`M_y,V,Rd = (W_pl,y − ρ·A_w²/(4·t_w))·f_y/γ_M0`, capped at `M_c,Rd`), hollow sections
the plainly conservative `(1 − ρ)·M_c,Rd`. Peak `M` and `V` are treated as coincident — conservative
for a UDL span, where they occur at different points. Hand-verified: IPE300 S275 with `V_Ed = 300 kN`
→ `ρ = 0.223`, `M_y,V,Rd = 164.2 kNm` (vs 172.7).

### 5.6 Deflection (SLS, optional)
Simply-supported UDL `δ = 5·w·L⁴/(384·E·I_y)` against limit `L/250` (default), using the
**characteristic** (unfactored) service load.

### 5.7 Reclaimed-steel knockdown
`knockdown ≤ 1.0` multiplies `f_y` (a condition/uncertainty proxy for reclaimed material) and is always
flagged. The knockdown is **per member**, derived from the pre-demolition audit (§3.1): condition ×
verification factor, or an explicit auditor value. A member with no audit data uses the run default
(`--knockdown`, default 1.0, which assumes the grade is confirmed by testing); set it lower for an
un-audited donor stock you don't yet trust.

Member status: `FAIL` if governing utilisation > 1, `REVIEW` if class 4, else `OK`.

## 6. Embodied carbon  (`core/carbon.py`)

Factors from `data/carbon/factors.csv` (ICE v3, 2019, UK structural steel): production `A1–A3 = 1.55`
kgCO₂e/kg, reuse process (clean/test/refabricate) `= 0.10` kgCO₂e/kg. The **material passport** reports,
per mapped member, mass, volume, new-build embodied carbon, reuse process carbon, the net saved, and
(when audited) the member's **verification basis and condition grade** (§3.1) — the provenance a
material passport is meant to carry.

## 7. The matcher  (`match/optimize.py`)

1. **Sparse feasibility mask.** A (supply, slot) pair is admissible only if the reclaimed member is long
   enough (`length ≥ required + 50 mm` cut tolerance) **and** passes the exact EN check for that slot's
   forces in **every** load combination of the envelope (§4); the governing combination is recorded and
   reported. Most pairs are infeasible and never enter the model — this tames the MILP size.
   Additionally, a **connection feasibility screen** (`core/connections.py`) compares each donor
   geometrically against the slot's *design section* — the section its connections were detailed
   around: wrong shape family (tube ↔ open) or more than 50 mm deeper → `incompatible`; markedly
   shallower, thinner web (bolt bearing), or narrower flange (seats/end plates) → `review`. Every
   assignment is annotated with the result (report "Connection" column); with `--connections`,
   incompatible pairs are excluded before matching. The screen also estimates each donor's **standard
   fin-plate shear capacity** (`standard_shear_capacity`: a single vertical row of M20 8.8 bolts in a
   10 mm S275 plate, rows from the clear web depth at p1 = 70/e1 = 40, per bolt the EN 1993-1-8
   Table 3.4 minimum of bolt shear `0.6·f_ub·A_s/γ_M2` and bearing `2.5·0.5·f_u·d·t/γ_M2` with the
   conservative end-row `α_b = 0.5`; hand-verified ≈ 183 kN / 3 rows for IPE300) and flags `review`
   when the slot's worst `V_Ed` exceeds it — a standard end connection won't carry the shear, so a
   bespoke one is needed; never a gate. Tubes get no capacity opinion (different typology). The
   tolerances and the standard detail are an explicit `ConnectionPolicy`. Connection *design* (welds,
   block tearing, moment connections, the bespoke cases) stays out of scope.
2. **Avoided-new baseline (per slot).** The honest CO₂ basis is the **lightest catalog section that
   passes the slot's exact check**, restricted to the **slot's own design standard** (a US slot's
   baseline is a W-shape, not a coincidentally-lighter IPE) **and shape family** (a hollow baseline
   only when the design section is a tube; open I/H otherwise — you would not have bought a tube for a
   W-shape slot). Using this rather than the donor's mass stops a heavy donor in a light slot from
   over-booking carbon as "saved".
3. **Net CO₂ saved (booked & reported).** `co2_saved = baseline_mass·A1A3 − used_mass·reuse_process −
   connection_refab`. The off-cut term is a **soft preference only** (the remainder returns to stock, it
   is not emitted) and steers the optimiser but is not booked.
4. **MILP** (PuLP/CBC): binary `x_ij`, each slot ≤ 1 supply, each supply used ≤ 1, maximising
   `Σ score·x` with `score = co2_saved − w_offcut·offcut_mass·saved_per_kg` (`w_offcut = 0.3`,
   `connection_refab = 5 kg` defaults). Only proven-`Optimal` CBC results are trusted; a timeout/error
   escalates to a **greedy fallback** that takes highest-score net-positive pairs first (it never books a
   net-negative match, mirroring the MILP). Reclaimed **supply is not standard-restricted** — reusing a
   donor across standards is legitimate.
5. **Cutting-stock (optional, `allow_cutting` / `--cut`).** Instead of one piece per donor, a donor may
   be cut into several pieces for several slots, bounded by its length:
   `Σ_j (required_len_j + 50 mm cut tolerance)·x_ij ≤ length_i`. The off-cut penalty is dropped (the
   remainder is genuinely reusable, so the bias against long stock disappears — this is the real fix for
   the off-cut-as-waste limitation); the objective books each filled slot's avoided-new saving, and each
   cut donor's leftover is reported as reusable remainder (`MatchResult.donor_leftover_mm`). The greedy
   fallback packs donors first-fit by descending score under the same length cap.

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
| Column area / floors | 9 m² / 1 or geometry | `--col-trib-area/--col-floors` | floors=1 **under-loads** lower columns (superseded by `--frame-analysis`) |
| Column axial source | per-member tributary | `--frame-analysis` (load path) | frame solve removes the tributary/floor estimate |
| Frame idealisation | simple braced (pinned beams, continuous columns, fixed base) | `core/frame.py` | gravity + EN 5.3.2 sway (EHF) + P-Δ; wind/seismic not yet |
| Column moment | 0 (pure axial) | `--col-ecc` | real frame moments not modelled |
| Global sway imperfection φ | 0 (off); EN value 1/200 | `--phi` | member-level notional moment, or **frame EHF + P-Δ** with `--frame-analysis` |
| Effective length k | 1.0 | — | pinned (conservative) |
| LTB C₁ | 1.0 (uniform) | — | conservative |
| 6.3.3 C_m factors | 1.0 (uniform moment) | — | Table B.3 upper bound (conservative) |
| Member axis rotation | default orientation | — | local→section axis mapping assumed |
| Compression-flange restraint | restrained (slab) | load model | **non-conservative if slab absent** — mitigated by the χ_LT warning + `--construction` |
| Construction stage | off | `--construction` / `--construction-live` | full dead + q_ca 0.75 kN/m², unrestrained (conservative) |
| Reclaimed knockdown | 1.0 | `--knockdown` | assumes grade confirmed |
| Carbon factors | ICE v3 | `factors.csv` | swap for Ökobaudat/Climatiq |

## 10. Validation

**Deterministic core, hand-verified (`tests/test_ec3.py`).** Against IPE300 section tables: `ε(355) =
0.814`; `N_t,Rd(S275) = 1479.5 kN`; `M_pl,Rd = 147.6 kNm (S235) / 172.7 kNm (S275)`; `V_pl,Rd(S235) =
348 kN`; flexural buckling `χ_z(L=4 m, S275) = 0.392`; LTB `χ_LT(L=6 m) ≈ 0.45` and monotone-decreasing
with span; deflection `δ ≈ 9.62 mm (w=10 N/mm, L=6 m)`. Knockdown scales utilisation by `1/k`.
The **6.3.3 interaction** is hand-validated end to end (IPE300 beam-column, N = 300 kN + M_y = 40 kNm,
L = 4 m: `χ_y = 0.9606`, `k_yy = 1.0358`, eq. 6.61 = 0.4510, governing eq. 6.62 = **0.6607**; adding
M_z = 10 kNm flips it to FAIL at 1.162 — full chain in the test comments).

**Matcher (`tests/test_match.py`).** Known-answer feasibility, one-use-each constraints, the avoided-new
basis (a giant donor in a small slot books the baseline, not its own mass), standard-restricted baseline,
degenerate-geometry safety, the greedy net-positive guard, the load-combination envelope (governing case
+ baseline passing every combination), and **cutting-stock** (one donor cut to fill several slots, length
capacity respected by both the MILP and the greedy fallback, leftover reported).

**Connection screen (`tests/test_connections.py`).** Family mismatch and over-deep donors are
incompatible; shallower/thin-web/narrow-flange donors are `review`; no design section → no opinion;
the screen gates only when enabled and otherwise annotates; policy tolerances are adjustable.

**HSS (`tests/test_hss.py`).** AISC v15 anchor conversion (HSS6X6X1/2), internal-part classification
(class 1 and class 4 walls), curve c both axes, RHS shear area, no-LTB bending (vs an LTB-reduced open
section at the same span), compression χ hand-recomputed, and the family-restricted baseline.

**Catalog integrity (`tests/test_sections.py`).** For all 711 rows (40 EU + 283 US W + 388 US HSS):
`mass ≈ 0.785·A`, `W_el,y ≈ I_y/(h/2)`, `i = √(I/A)`, `W_el,z ≈ I_z/(b/2)`, `W_pl ≥ W_el` (worst real
deviation ≈ 1.5 %). HSS use the AISC mass basis `0.785·A/0.93` (nominal weight from the nominal wall,
properties from the design wall `t_des = 0.93·t_nom`).

**Load-combination envelope (`tests/test_match.py`, `tests/test_loads.py`).** The matcher checks every
combination and reports the governing one; the avoided-new baseline must pass the whole envelope (a
sway-imperfection moment forces a heavier baseline than gravity alone); `combination_loads` adds the
EN 5.3.2 case for columns only when `φ > 0`.

**Heavy sections (`tests/test_ec3.py`).** `nominal_fy` thickness bands (EN grades reduce for `t > 40 mm`,
ASTM unchanged); `t_f > 40/100 mm` shifts the buckling curve (lower χ), and `check_member` flags the
heavy section and the EN `f_y` reduction.

**Frame analysis (`tests/test_frame.py`).** Topology snapping (shared endpoints collapse to one node,
tolerance behaviour, members without geometry skipped); the solve **reproduces the closed-form
simply-supported result** a one-bay portal must give (`M = wL²/8`, `V = wL/2`, agreeing with
`AnalyticBackend`); and column axial **accumulates down a multi-storey stack** (lower lift = 2× the upper),
both compared against hand statics. End-to-end: `run_pipeline(frame_analysis=True)` on a portal reuses
stock, and a coordinate-free model falls back to the analytic path without error. (Worked check on a
2-bay × 2-storey frame: interior column 332 kN vs corner 166 kN, all matching `p·trib·span/2` by hand.)
**Lateral:** with `φ > 0` the solve adds a sway (EHF) combination per direction and a P-Δ pass — a braced
bay carries the notional sway as **brace axial** and the sway case changes the brace force vs. gravity
alone; `φ = 0` leaves a single gravity combination (default unchanged). **Wind:** `wind_node_forces` lumps
`q·width·h_trib` onto a level's column tops (exact arithmetic check on a 3-D box), returns nothing for a
planar frame, and `wind_kpa > 0` adds the wind combinations + changes the column axial vs. gravity.
**Continuous members:** `expand_spans` splits a 2-span beam into `B#0`/`B#1` at the right midpoint, and in
the solve the **interior column carries both spans' reactions** (≈ 2× an end column) while each span keeps
its own `wL²/8`; `run_pipeline` then yields one slot per span. **Seismic:** `seismic_node_forces` on a
2-storey box gives a base shear `Cs·ΣW` distributed inverted-triangular (roof force = 2× the floor force),
and `seismic_cs > 0` adds the `seismic X/Y` design situations.

**Whole suite:** 127 tests, ruff clean.

## 11. ML modules (exploratory, not in the certified path)

`ml/` is an **exploratory side-study and is not wired into the matching pipeline** — the result path is
entirely deterministic (sections → loads → EN 1993 → carbon → MILP). Three modules:

- **Capacity surrogate** (`ml/surrogate.py`, XGBoost) imitates the deterministic utilization for a fast
  pre-screen. Its reported test R² (~1.0) is **circular**: the labels are produced by the EN 1993
  checker itself over a synthetic sweep, so a high score only shows the model can reproduce the checker,
  not real-world predictive power. It is never authoritative (CLAUDE.md rule 3).
- **Reuse score** (`ml/reuse_score.py`) is a transparent weighted heuristic (section standardization ×
  length usability) — the honest, non-formula judgement, replaceable by a trained model when real reuse
  outcomes are available.
- **Clustering** (`ml/clustering.py`) groups similar sections (KMeans) for exploration.

Wiring any of these into the pipeline (surrogate as a pre-filter, reuse-score as an objective term) is a
deliberate future decision, logged in `FUTURE_IMPROVEMENTS.md` #7 — not a default.

## 12. Out of scope (explicit non-claims)

Connection design and capacity; **modal/response-spectrum** seismic analysis (the frame analysis of §4.1
models gravity, the EN 5.3.2 sway imperfection, wind, and a **simplified EN 1998 lateral force** seismic
case with a 2nd-order P-Δ solve, but not a modal spectrum, accidental torsion, or pattern combinations —
the `combos` parameter is the hook); member rotation about its own
axis (the biaxial check of §5.5 assumes the default local→section axis orientation);
fatigue, corrosion and weldability of aged steel;
effective-section (class 4) design. (Cutting one donor into several slots is available as the optional
cutting-stock mode, §7 point 5; per-member forces from a global solve — with sway, wind, seismic and P-Δ —
via `--frame-analysis` `--phi` `--wind` `--seismic`, §4.1.) See `FUTURE_IMPROVEMENTS.md` for the backlog.
