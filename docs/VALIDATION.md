# Validation

This document validates the deterministic core — the load path, the EN 1993-1-1 resistance checks, and
the catalog section properties — against **hand calculations** and **published section tables**. Every
number below is reproduced by an automated test, so CI guards them against regression. It complements
[METHODOLOGY.md](METHODOLOGY.md), which maps each Eurocode clause to the code.

> Scope: this validates the *engine's arithmetic*, not the engineering judgement around reuse. The tool
> remains member-level pre-feasibility — not connection design, not code-certified (see the README).

Reproduce everything:

```powershell
pytest tests/test_validation.py tests/test_ec3.py tests/test_frame.py -q
```

The reference section throughout is **IPE 300** (S275 unless noted). Catalog properties used:
`A = 5380 mm²`, `W_pl,y = 628×10³ mm³`, `I_y = 83.56×10⁶ mm⁴`, `i_z = 33.5 mm`, `A_v ≈ 2567 mm²` —
these match the ArcelorMittal / eurocodeapplied IPE tables (the catalog rows were cross-checked against
that source; see [FUTURE_IMPROVEMENTS.md](../FUTURE_IMPROVEMENTS.md) #4).

---

## §1 End-to-end: loads → frame → forces → check

A single 6 m bay (two IPE300 columns, one IPE300 beam), default area-load model.

**Load path (hand calc):**
- Factored area load (EN 1990 6.10, defaults g_k = 3.5, q_k = 3.0 kPa):
  `1.35·3.5 + 1.5·3.0 = 9.225 kPa`.
- Line load on the beam (3 m tributary): `w = 9.225 kPa × 3 m = 27.675 N/mm`.
- Simply-supported bay: `M = wL²/8 = 27.675 × 6000² / 8 = 124.5 kNm`; `V = wL/2 = 83.0 kN`.
- Each beam end delivers `wL/2 = 83 kN` to its column; in a two-storey stack the lower column carries
  both floors → `166 kN` (twice the upper). 

**Code result:** the PyNite frame solve returns `M_y,Ed = 124.5 kNm`, `V_z,Ed = 83 kN`, beam axial ≈ 0,
and the column axials accumulate `83 → 166 kN` down the stack — matching hand statics.
(`tests/test_frame.py`, `tests/test_validation.py::test_worked_frame_load_path_to_check_end_to_end`.)

**Resulting EN check (IPE300, S275, slab-restrained):** bending utilisation
`M_Ed / M_c,Rd = 124.5 / 172.7 = 0.72`; the **SLS deflection check governs the member at ~0.78**
(`δ = 18.8 mm` vs `L/250 = 24 mm` under the service load) — a good reminder that serviceability, not
strength, often decides a reuse beam.

## §2 Bending & shear resistance (Class 1 cross-section)

- `M_c,Rd = W_pl,y · f_y / γ_M0`. S235: `628×10³ × 235 = 147.6 kNm`. S275: `628×10³ × 275 = 172.7 kNm`
  (`γ_M0 = 1.0`).
- `V_c,Rd = A_v · f_y / (√3 · γ_M0)`. S235: `2567 × 235 / √3 = 348 kN`.

These match the published IPE300 table values. (`tests/test_ec3.py`, `tests/test_validation.py::test_worked_beam_resistances_match_section_tables`.)

## §3 Column flexural buckling (EN 1993-1-1 §6.3.1)

IPE300, S275, `L = 4000 mm`, `k = 1`, weak (z) axis, buckling **curve b** (`h/b = 2.0 > 1.2`):

- Slenderness `λ_z = L / i_z = 4000 / 33.5 = 119.4`.
- `λ₁ = 93.9·ε`, `ε = √(235/275) = 0.924` → `λ₁ = 86.8`; non-dimensional `λ̄_z = 119.4 / 86.8 = 1.376`.
- Curve b `α = 0.34`: `Φ = 0.5[1 + 0.34(1.376 − 0.2) + 1.376²] = 1.647`.
- `χ_z = 1 / (Φ + √(Φ² − λ̄²)) = 1 / (1.647 + 0.905) = 0.392`.
- `N_b,Rd = χ_z · A · f_y / γ_M0`.

**Code result:** `χ_z = 0.392`, `N_b,Rd = χ_z · A · f_y` exactly.
(`tests/test_ec3.py::test_buckling_chi_weak_axis_ipe300_s275`, `tests/test_validation.py::test_worked_column_flexural_buckling`.)

## §4 Lateral-torsional buckling & deflection

- **LTB** (IPE300, S275, `L = 6 m`, uniform moment, `C1 = 1`): `χ_LT ≈ 0.45`; a longer unrestrained
  span lowers it monotonically. The check uses simplified (conservative) `M_cr`. The default run also
  surfaces this "if unrestrained" value on slab-restrained beams as a construction-stage warning.
- **Deflection** (SLS): `δ = 5wL⁴/(384EI)`. For `w = 10 N/mm`, `L = 6 m`: `δ = 9.62 mm` vs `L/250 = 24 mm`.

(`tests/test_ec3.py::{test_chi_lt_handcalc_ipe300, test_deflection_check_simply_supported}`.)

---

## Summary

| Quantity | Hand value | Code | Source check |
|----------|-----------|------|--------------|
| Factored area load | 9.225 kPa | 9.225 | test_validation §1 |
| Beam moment `wL²/8` | 124.5 kNm | 124.5 | test_frame / test_validation |
| Beam shear `wL/2` | 83.0 kN | 83.0 | test_frame |
| Column accumulation | 83 → 166 kN | 83 → 166 | test_frame |
| `M_c,Rd` IPE300 S275 | 172.7 kNm | 172.7 | test_ec3 / test_validation |
| `V_c,Rd` IPE300 S235 | 348 kN | 348 | test_ec3 / test_validation |
| `χ_z` (L=4 m, curve b) | 0.392 | 0.392 | test_ec3 / test_validation |
| `χ_LT` (L=6 m) | 0.45 | 0.45 | test_ec3 |
| Deflection `5wL⁴/384EI` | 9.62 mm | 9.62 | test_ec3 |

**Residual:** the validation above is hand-calc + section-table based. A future addition is a single
*published textbook frame* (e.g. an SCI/Eurocode design guide worked example) run start-to-finish, to
corroborate the combined N+M interaction against an external reference rather than internal hand calcs.
