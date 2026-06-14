# Cross-software force benchmark — canonical 2-bay frame

Per-member governing design forces (ULS gravity) on the *same* snapped topology. `%Δ` is each solver vs the reference (**pynite**). N in kN, M in kNm, V in kN.

## analytic vs pynite
- 4/7 force components agree within 2 %.
- 3 component(s) outside 2 %:

| Member | Comp | reference | value | %Δ |
| --- | --- | --- | --- | --- |
| C0 | N | 83025 | 0 | +100.0 |
| C1 | N | 166050 | 0 | +100.0 |
| C2 | N | 83025 | 0 | +100.0 |

## sap2000 vs pynite
- 7/7 force components agree within 2 %.
- 0 component(s) outside 2 %.

## Per-member forces

| Member | analytic N(kN) | analytic M(kNm) | analytic V(kN) | pynite N(kN) | pynite M(kNm) | pynite V(kN) | sap2000 N(kN) | sap2000 M(kNm) | sap2000 V(kN) | analytic %ΔN | analytic %ΔM | analytic %ΔV | sap2000 %ΔN | sap2000 %ΔM | sap2000 %ΔV |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C0 | 0 | 0 | 0 | 83 | 0 | 0 | 83 | 0 | 0 | -100.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 |
| C1 | 0 | 0 | 0 | 166 | 0 | 0 | 166 | 0 | 0 | -100.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 |
| C2 | 0 | 0 | 0 | 83 | 0 | 0 | 83 | 0 | 0 | -100.0 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 |
| B0 | 0 | 125 | 83 | 0 | 125 | 83 | 0 | 125 | 83 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | -0.0 |
| B1 | 0 | 125 | 83 | 0 | 125 | 83 | 0 | 125 | 83 | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 | -0.0 |


_solved with SAP2000 27.1.0 (experimental OAPI backend, gravity only); validated 2026-06-14: SAP2000 matches PyNite within 2 % on all components (parity test PASSED)._
