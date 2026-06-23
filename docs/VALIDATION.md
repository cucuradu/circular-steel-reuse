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
that source; see [OVERVIEW.md](OVERVIEW.md) §11).

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

## §5 Worked example — the whole pipeline, start to finish

The sections above validate each check in isolation. This worked example validates the **complete
pipeline as one run** (`tests/test_worked_example.py`): two extraction JSONs go in, and mapping →
loads → forces → EN 1993-1-1 checks → MILP matching → carbon accounting come out, with **every stage
asserted against the hand chain below** (statics are closed-form; resistances use the same published
ArcelorMittal table values cross-checked in §2–§3).

**The bay.** New design: one **IPE300** floor beam (S275, `L = 6 m`, slab-restrained, 3 m tributary)
on two **HEB200** columns (S275, `L = 3 m`, 9 m² tributary each). Donor stock: one **IPE330 × 7 m**
and two **HEB220 × 3.2 m**, all S275, no audit knockdown.

**1 — Actions (EN 1990 6.10, office defaults):**
`9.225 kPa = 1.35·3.5 + 1.5·3.0` → beam `w = 27.675 N/mm`,
`M_Ed = wL²/8 = 124.5375 kNm`, `V_Ed = wL/2 = 83.025 kN`; column `N_Ed = 9.225 × 9 = 83.025 kN`;
service load `w_ser = 6.5 × 3 = 19.5 N/mm`.

**2 — Donor beam check (IPE330, Class 1):**
`M_c,Rd = 804×10³ × 275 = 221.1 kNm` → bending utilisation `124.5375 / 221.1 = 0.5633` (**governs**);
deflection `δ = 5·19.5·6000⁴/(384·E·117.7×10⁶ mm⁴) = 13.3 mm` vs 24 mm → 0.555; shear
`83.0 / 489.1 = 0.170`.

**3 — Donor column check (HEB220, curve c both `h/b = 1`):**
`N_cr,z = π²·210000·28.43×10⁶ / 3000² = 6547 kN`, `λ̄_z = √(9100·275/6.547×10⁶) = 0.618`,
`Φ = 0.794`, `χ_z = 0.7745` → `N_b,Rd = 1938 kN` → utilisation `83.025 / 1938 = 0.0428`.

**4 — Matching.** Only the 7 m donor fits the 6 m beam slot (`required + 50 mm`); the MILP fills all
three slots: IPE330→beam (off-cut 1000 mm), HEB220→each column (off-cut 200 mm). Connection screen:
IPE330 stands +30 mm deeper than IPE300 (≤ 50 mm) and HEB220 +20 mm deeper than HEB200 → all `ok`.

**5 — Avoided-new baselines (the honest CO₂ basis):**
- Beam: the lightest EU section passing *all* checks is **IPE300 itself** — IPE270 fails the SLS
  deflection check (`δ = 27.1 mm > L/250 = 24 mm`), everything lighter fails bending. Baseline mass
  `42.2 × 6 = 253.2 kg`.
- Column: **IPE160** (15.8 kg/m), the lightest EU row, passes (`λ̄_z = 1.875`, curve b,
  `χ_z = 0.235`, `N_b,Rd = 129.8 kN ≥ 83.0 kN`). Baseline mass `15.8 × 3 = 47.4 kg`.

**6 — Carbon (ICE v3 factors: A1–A3 1.55, reuse process 0.10, connection refab 5 kg):**
`beam: 253.2·1.55 − 294.6·0.10 − 5 = 358.00 kg` · `column: 47.4·1.55 − 214.5·0.10 − 5 = 47.02 kg`
(each) → **total 452.04 kg CO₂e**, which is exactly what the pipeline books and reports.

Note what the example exercises beyond arithmetic: the deflection-governed baseline (IPE270 is
strength-adequate but serviceability-inadequate), the length feasibility cut, the shape-aware
connection annotations, and the avoided-new basis (the saving is measured against IPE300/IPE160,
*not* the heavier donors actually used).

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
| Worked-example beam util | 0.5633 | 0.5633 | test_worked_example |
| Worked-example column util | 0.0428 | 0.0428 | test_worked_example |
| Worked-example CO₂ saved | 452.04 kg | 452.04 | test_worked_example |
| 6.3.3 eq. (6.62), IPE300 beam-column | 0.6607 | 0.6607 | test_ec3 |

The **full 6.3.3 beam-column interaction** (eq. 6.61/6.62, Annex B Method 2, `C_m = 1.0`) is
hand-validated in `tests/test_ec3.py`: IPE300 S275, L = 4 m, N = 300 kN, M_y = 40 kNm, restrained
flange → χ_y = 0.9606, χ_z = 0.3924, k_yy = 1.0358, eq. 6.61 = 0.4510, governing eq. 6.62 = **0.6607**
(every intermediate is in the test-file comment); a biaxial variant adds M_z = 10 kNm and flips the
member to FAIL (eq. 6.62 = 1.162), exercising the `k_yz`/`k_zz` terms.

## §6 Independently published worked examples

§1–§5 validate the engine against our *own* hand algebra. This section closes that gap: the engine is
cross-checked against worked examples published by external authorities, every one reproduced by a test
in `tests/test_published_examples.py` (both sources are free; γ_M0 = γ_M1 = 1.0, matching the engine).

Sources: **SCI P387** *Steel Building Design: Worked examples for students* (Eurocodes + UK NA) and
**ArcelorMittal/SECEU MSB04** *Multi-Storey Steel Buildings, Part 4: Detailed Design* (base EN 1993-1-1).

| Source | Member | Quantity | Published | Engine |
|--------|--------|----------|-----------|--------|
| P387 Ex01 | 457×191×82 UKB S275 | M_c,Rd / V_c,Rd / δ | 503 kNm / 756 kN / 13.6 mm | match |
| P387 Ex02 | 457×191×98 UKB | M_cr (C1=1.127, z_g=0) | 534.0 kNm | <1 % |
| MSB04 A.1 | IPE 330 S235 | M_c,Rd / V_pl,Rd | 189.0 kNm / 417.9 kN | match |
| P387 Ex05 | 254×254×73 UKC S275 | χ_z / N_b,z,Rd | 0.61 / 1562 kN | <1 % |
| MSB04 A.5 | HE 300 B S235 | χ_y / χ_z / N_b,Rd (k_z=0.7) | 0.808 / 0.671 / 2349 kN | <0.5 % |

Two engine refinements came out of this exercise and are now in place:

- **LTB load height.** `M_cr` now carries the EN load-height terms (`C2`, `z_g`); the member LTB check
  defaults to the destabilising top-flange case (`z_g = +h/2`), reproducing MSB04 A.1's behaviour. The
  earlier shear-centre (`z_g = 0`) form over-stated `M_cr` for top-flange-loaded unrestrained beams.
- **f_y product banding.** Nominal `f_y` now follows the finer EN 10025-2/-3 product bands (16/40/63/80
  mm) rather than EN 1993-1-1 Table 3.1's two bands — e.g. S275 = 265 for 16 < t ≤ 40 mm — matching
  P387 and the UK NA. (All §1–§5 reference sections have t_f ≤ 16 mm, so those numbers are unchanged.)
