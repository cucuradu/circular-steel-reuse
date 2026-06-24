# Roadmap: from clever demo to certifiable open reference tool

> **Purpose.** The forward plan that turns the current 1-to-1 matcher into a *certifiable, publishable,
> market-credible* reference tool — to "Paper #1-level completeness" in its own domain. This is the
> goal-specific roadmap behind [POSITIONING.md](POSITIONING.md). It is **separate from**
> [../FUTURE_IMPROVEMENTS.md](../FUTURE_IMPROVEMENTS.md), which is the broad engine/methodology backlog;
> the two cross-reference and must not duplicate. When an item here ships, move it to `CHANGELOG.md` and
> delete it (same rule as FUTURE_IMPROVEMENTS).
>
> For the **wide-angle view** — every stage of the reuse process the tool should guide the engineer
> through (building-death → trigger → PDA → testing → matching → fab → verify → passport), and where
> reuse really fails — see [PROCESS_MAP.md](PROCESS_MAP.md). This roadmap is the *build plan*; the
> process map is the *scope it serves*.

## Why "Paper #1-level completeness" is the bar

Zou et al.'s single-case Revit prototype reads as *complete in its domain* not because it does more,
but because it is **transparent, measured, reproducible, and documented**:

- a working tool inside the designer's environment,
- an **open-data** backing for its factors,
- a **measured coverage** number (82% rule mapping),
- a **measured time-to-result** (manual 290–380 min → <30 min reused),
- **one fully-documented validation case**, and
- **end-to-end traceability** from input to result.

This tool already beats it on engineering depth (it *verifies and optimises*, it doesn't only account).
The gap is entirely in that completeness checklist — plus the interop the 2026 competitor field now
makes table-stakes. The competitor scan (see POSITIONING §4) sharpens the target: ēfestos/Loopfront own
*inventory + condition AI*; WSP/R.E.A.C.T. own *slick optimisation*; **nobody open owns *verifiable*
matching**. Every phase below defends that wedge.

Severity / priority follows the project register: 🔴 blocks the certifiable claim · 🟠 important ·
🟡 polish.

---

## Every step in plain English

Scan this first. Each row is one step; the detail is in the phase sections below. Iterate here, then
push changes down into the detail.

| Step | In plain words | Why it matters |
|------|----------------|----------------|
| **1.1 Evidence package** 🔴 | One button → one file showing every input, every code check, and the proof the answer is sound. | An engineer can review the whole job without redoing the maths. This *is* "certifiable". |
| **1.2 Open rules + reject log** 🔴 | Move the hidden rules (which steel name = which section, which carbon number) into a plain table anyone can open and challenge. Keep a list of every donor it couldn't use, and why. | Turns "trust the code" into "see and question the rule." Nothing is silently dropped. |
| **2.1 Coverage number** 🟠 | Show two honest %: how many demand spots got filled, how many donor pieces the tool understood. | One honest number is what made the reference paper believable. Don't hide misses. |
| **2.2 Speed number** 🟠 | Measure seconds from file-in to report-out, vs how long a human would take by hand. | "1000 members in N seconds" is a strong, repeatable, publishable claim. |
| **2.3 One full case study** 🟠 | Walk one real building start-to-finish with every number shown. | People believe a worked example, not claims. Anchors the thesis + the demo. |
| **3.1 Import other apps' stock** 🟠 | Let inventory lists from ēfestos / Loopfront / Stockmatcher feed straight in. | You consume inventory you don't own — no retyping. Big reach, little code. |
| **3.2 Export usable schedule** 🟡 | Output the answer as the Excel / Revit schedule engineers already use, plus a leftover-stock list. | Makes the tool a drop-in step in real workflows, not an island. |
| **4.1 Match the rulebooks** 🟠 | Arrange the evidence file under the exact headings SCI P427 / BS 8001 expect. | An assessor's checklist is half-filled already — closer to a real sign-off. |
| **4.2 Show the uncertainty** 🟡 | Print the carbon number with a "give or take" range and what drives it most (weight > count > distance). | An honest claim states its own wobble. Builds trust. |

**The order, in one line:** do 1.1 + 1.2 first (the only must-haves — they unlock the "certifiable"
claim), then 2.x measures and proves it, 3.x connects it to the market, 4.x polishes trust. Most of
2–4 is *surfacing things the code already does*, not new building.

---

## Phase 1 — Certifiable core *(makes it publishable)*

The minimum that lets the tool honestly claim "certifiable-ready evidence" (POSITIONING §3).

### 1.1 🔴 Per-run evidence package export
A single signable artifact (HTML/PDF + machine-readable JSON sidecar) bundling, per run:
- every input (donor + demand + loads + National Annex), with hashes;
- the chosen weights/objective and catalogue + carbon-factor **versions**;
- per-assignment **EN 1993 pass-evidence** — governing clause, utilisation, χ_LT, governing combination;
- the **`verify_match` certificate** result (feasible + no improving single move).

*Justification:* this is what an engineer/notified body reviews instead of re-deriving (POSITIONING §3,
third-party path). Most pieces already exist in [`match/optimize.py`](../src/steelreuse/match/optimize.py)
and [`llm/report.py`](../src/steelreuse/llm/report.py) — this is assembly + a stable schema, not new science.

**Acceptance:** given a demo run, the package alone lets a reviewer re-check any assignment by hand and
reproduce the headline CO₂ number to the rounding stated.

### 1.2 🔴 Externalised, versioned rule-mapping + mismatch log
- Move section-name mapping and carbon-factor/grade selection out of implicit code into a
  **user-inspectable, version-stamped** rule table (Paper #1's configurable rule tables).
- Emit a **mismatch log**: every unmapped section, quarantined donor, infeasible pair — *with the
  reason* (extends `diagnose_match`).

*Justification:* POSITIONING §2. Turns "trust the code" into "see and challenge the rule." Mapping logic
exists in [`core/sections.py`](../src/steelreuse/core/sections.py); the work is externalising + stamping.

**Acceptance:** a reviewer can open the rule set, see its version, and a mismatch log accounts for 100%
of donor rows (mapped / fuzzy / unknown / quarantined) with a reason each.

---

## Phase 2 — Measured & validated *(matches Paper #1's completeness markers)*

### 2.1 🟠 Coverage metric
Report, per run: **% demand slots matched** and **% donor sections mapped** (Paper #1's 82% analogue).
Surface in the report header and the evidence package.

*Justification:* a single honest coverage number is what made Paper #1 legible. Cheap; data already
computed in `diagnose_match` (`donors_eligible`, `n_unmatched`).

**Acceptance:** coverage figures appear on every run and reconcile with the assignment counts.

### 2.2 🟠 Time-to-result metric
Benchmark engine wall-clock from JSON-in to report-out on the bundled demo + the 1000-member case, and
state it against a plausible manual baseline (Paper #2 / industry).

*Justification:* Paper #1's headline was *time*, not accuracy. A reproducible "<N seconds for 1000
members" is a strong, honest, publishable claim. Benchmark scaffolding exists under
[`benchmark/`](../src/steelreuse/benchmark/).

**Acceptance:** a committed benchmark script prints the timing; the number is quoted in CASE_STUDY.

### 2.3 🟠 One end-to-end case study to Paper #1's structure
A single, fully-documented donor→receiver case (LoD-300-equivalent): inputs, rule coverage, time,
assignments, carbon saved, the certificate, and the explicit non-claims. Align/supersede
[CASE_STUDY.md](CASE_STUDY.md).

*Justification:* one transparent case is the completeness marker, not a benchmark suite. Anchors the
thesis chapter and the demo.

**Acceptance:** a reader can follow the case from raw input to signed evidence without leaving the doc.

---

## Phase 3 — Interop wedge *(position vs the market without rebuilding it)*

The competitor scan says: don't build inventory or a marketplace — **connect to them.**

### 3.1 🟠 Import adapters from inventory players
IFC already ingests via [`ifc_extract.py`](../src/steelreuse/ifc_extract.py). Add a **documented input
schema** + thin adapters so exports from ēfestos / Loopfront / Stockmatcher / a Re-Bridge catalogue can
feed the matcher as donor supply.

*Justification:* POSITIONING §5 — the tool *consumes* inventory it doesn't own. Largest reach for least
new code; mostly schema + mapping.

**Acceptance:** at least one external/illustrative inventory format maps to the donor schema with a
documented field correspondence.

### 3.2 🟡 Marketplace-friendly result export
Export assignments + carbon results as the **Excel/Revit-schedule** shape the market expects (the WSP
tool's de-facto output), and unmatched donors as a re-loadable supply list.

*Justification:* meets engineers where they are; makes the tool a drop-in step, not an island. Write-back
to Revit already exists; this is the no-Revit equivalent.

**Acceptance:** a matched run produces a schedule another tool/engineer can open and act on.

---

## Phase 4 — Credibility hardening *(turns the demo into a tool engineers trust)*

### 4.1 🟠 Align outputs to SCI P427 / BS 8001 evidence requirements
Map the evidence package fields to the headings SCI P427 / BS 8001 expect, so the export *is* the
documentation an assessor wants (POSITIONING §3, third-party path). Documentation + field-mapping work,
not new analysis.

**Acceptance:** a checklist in the doc shows which P427/BS 8001 evidence items the package satisfies and
which remain the engineer's (coupon programme, corrosion/fatigue survey, connection design).

### 4.2 🟡 Sensitivity as standard report output
[`sensitivity.py`](../src/steelreuse/sensitivity.py) already does tornado + Monte-Carlo. Promote it to a
standard section of the report, leading with **Paper #2's driver ranking — weight > #elements >
transport distance** — as the headline sensitivities so the carbon claim carries its uncertainty.

**Acceptance:** the report shows the CO₂ headline with a P5–P95 band and the top drivers, by default.

---

## Explicitly deferred (not on this roadmap)

- **Inventory capture + condition-AI** — ēfestos / Loopfront territory; the PDA stays a *typed input*,
  not an AI inspection.
- **Marketplace** — Re-Bridge / Loopfront / Madaster own it; we interop (Phase 3), not rebuild.
- **Connection *design*** — screening only ([`core/connections.py`](../src/steelreuse/core/connections.py));
  design stays the engineer's.
- **Issuing certification** — the tool produces evidence; it never grants a certificate.
- **Stock / hubs** — out of scope by your decision (tracked separately, not here).

---

## Sequencing rationale (the lazy path)

Phase 1 is the only **🔴** work and unlocks the publishable claim — do it first and most of the
"certifiable" story is told. Phases 2–4 are largely *surfacing and documenting capabilities that already
exist* (coverage from `diagnose_match`, timing from `benchmark/`, sensitivity from `sensitivity.py`,
write-back patterns) rather than new engineering — which is why the jump to Paper #1-level completeness
is mostly assembly and honesty, not a rebuild.
