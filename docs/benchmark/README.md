# SAP2000 cross-software benchmark — how to run

This folder holds the **cross-software force benchmark** (see [../OVERVIEW.md](../OVERVIEW.md) §11): the
same frame solved with three force methods — **analytic** (`wL²/8`), **PyNite** (the free default
solver), and **SAP2000** (the professional solver, via its OAPI) — with a per-member comparison table.
Its job is *external validation*: if PyNite and SAP2000 agree, the project's force numbers are trusted
by an independent commercial tool, not just by our own hand-calcs.

There are **two different things** you can do with the SAP2000 backend. Don't confuse them.

| | What frame it solves | Purpose |
|---|---|---|
| **The benchmark** (`steelreuse-bench-sap2000`) | a tiny, hand-checkable **2-bay toy frame** (5 members) | prove PyNite ≈ SAP2000 on something you can verify by hand → the reference comparison table |
| **The pipeline backend** (`--solver sap2000`) | your **real receiver (demand) model** — every extracted member | actually source member forces for the matching from SAP2000 instead of PyNite |

---

## Before you start (once)

1. **Check the licence.** Open SAP2000 → **Help → About**. If it says **"Educational"**, the model-size
   cap blocks the OAPI — the backend will fall back to analytic. A full trial/licence works.
2. **The COM bridge is already installed** in the signed venv (`comtypes`, `pywin32`). If you ever
   rebuild the venv, reinstall with:
   ```
   & "C:\Users\Radu\Projects\Python\.venv-signed\Scripts\python.exe" -m pip install comtypes pywin32
   ```

> **Why `python -m …` and not the `steelreuse-bench-sap2000` command?** On this WDAC-locked machine the
> pip-generated `*.exe` launchers are unsigned and blocked. Always call through the **signed venv
> Python** with `-m`. On a normal machine the console commands (`steelreuse-bench-sap2000`,
> `steelreuse …`) work directly.

For brevity below, set the interpreter once per PowerShell session:
```powershell
$py = "C:\Users\Radu\Projects\Python\.venv-signed\Scripts\python.exe"
```

---

## A. Run the benchmark

With SAP2000 **closed** (the backend starts its own instance):
```powershell
& $py -m steelreuse.benchmark.sap2000_bench --out docs/benchmark
```
This writes / overwrites:
- `docs/benchmark/forces_compare.csv` — machine-readable (SI units: N, N·mm).
- `docs/benchmark/forces_compare.md` — the reference table (kN, kNm) with `%Δ` vs PyNite.

Options: `--reference pynite|analytic|sap2000` (which solver the `%Δ` is measured against).

**What "good" looks like:** the SAP2000 columns fill in, and `sap2000 %Δ` is ~0 % for the beams' M and
V and for the columns' axial N. A few-percent spread is normal solver numerics; a large, one-sided
gap usually means a sign/axis convention mismatch to fix in `core/frame_sap2000.py` (see "If forces
disagree" below). Finally, fill the SAP2000 version into the `_SAP2000 version: …_` line at the bottom
of the `.md` for the record.

### A2. Compare forces on your REAL building (PyNite vs SAP2000, member by member)

The benchmark can run on an actual extracted demand model instead of the toy frame — this is how you
sanity-check (and tweak) the PyNite forces against SAP2000 on the building itself:
```powershell
& $py -m steelreuse.benchmark.sap2000_bench `
    --demand data/case_study/demand.json `
    --out docs/benchmark/test4
```
Both solvers split continuous members **identically**, so the 492 sub-members line up one-to-one. The
console prints e.g. `sap2000 vs pynite: 470/492 within 2 %, 22 worse`; `forces_compare.md` lists the
**worst-disagreeing members** (so you know exactly where to look) and `forces_compare.csv` holds every
member's N/M/V from both solvers. (Analytic is omitted here — its un-split member keys wouldn't align.)
A large, consistent gap on many members points to the sign/axis mapping; a few scattered ones are
usually genuine modelling/numerical differences worth a look.

## B. Confirm agreement automatically (the parity test)

```powershell
& $py -m pytest tests/test_sap2000_parity.py -v
```
On a machine **without** SAP2000 this test **skips** (CI stays green). With SAP2000 present it solves
the canonical frame both ways and asserts every member's N/M/V agree within **2 %**. If it fails, it
prints exactly which member and component disagree.

## C. Use SAP2000 on your real receiver model (optional)

To source the actual matching forces from SAP2000 instead of PyNite, add `--solver sap2000` to a normal
frame-analysis run:
```powershell
& $py -m steelreuse.cli `
    --donor  data/case_study/donor.json `
    --demand data/case_study/demand.json `
    --frame-analysis --solver sap2000 `
    --out reports/report_sap2000.html
```
The console prints `Forces: frame analysis (sap2000) …`. If SAP2000 is unavailable, or the model is too
irregular to solve cleanly, it prints the reason and **falls back to the analytic per-member forces** —
your run still completes. Default (`--solver pynite`, or omitting the flag) is unchanged.

---

## If forces disagree (the one thing to watch)

SAP2000 and PyNite use **different sign/axis conventions**. SAP2000 axial `P` is **tension-positive**;
the project (and PyNite) use **compression-positive**. That single flip is handled in the
`_SapMemberForces` adapter in `core/frame_sap2000.py` (axial = `−P`; major moment ← `M3`, minor ← `M2`;
shears ← `V2`/`V3`). If the parity test reports a clean sign-flipped or wrong-axis mismatch, that
adapter is where to correct the mapping — the benchmark is precisely the tool that surfaces it.

## Troubleshooting: "too many SAP2000 instances open"

The trial caps concurrent instances (e.g. 3). The backend starts its **own hidden** SAP2000 per run
and closes it automatically — so a normal run leaves nothing behind. Instances only pile up if a run is
**interrupted** (Ctrl-C, a crash, or closing the terminal mid-solve) before cleanup runs. To clear any
strays:
```powershell
Get-Process -Name "SAP2000*" -ErrorAction SilentlyContinue | Stop-Process -Force
```
(They are blank automation instances — no model of yours is in them.) If you'd rather *watch* SAP2000
work, the backend runs hidden by default; pass `visible=True` to `sap2000_session()` only when
debugging. Don't keep your own SAP2000 model open in a 4th window while running, or you'll hit the cap.

## Scope (what this backend does and does not do)

- **Does:** the ULS **gravity** combination (`γ_G·dead + γ_Q·live`) on connectable frames.
- **Does not (yet):** wind, earthquake, sway imperfection, or 2nd-order (P-Δ). Asking for any of these
  with `--solver sap2000` is **refused with a clear message** (it does not silently give a wrong
  answer) — use `--solver pynite` for those cases.
