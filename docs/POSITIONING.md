# Positioning: literature rebase & market gap analysis

> **What this is.** A one-pass analysis that (a) mines the three reference papers — chiefly the
> BIM/Revit carbon paper — for methods that make this tool's results *certifiable*, (b) maps the
> real 2026 startup/competitor field, and (c) **redefines the realistic scope and end-goal** of the
> current 1-to-1 matcher. Stock/hub features are deliberately out of scope here.
>
> The forward-looking work this implies lives in
> [ROADMAP_CERTIFIABLE_REUSE.md](ROADMAP_CERTIFIABLE_REUSE.md). The full engineer journey this tool
> should guide — every stage from a building's death to verified reuse, and where reuse really fails —
> is mapped in [PROCESS_MAP.md](PROCESS_MAP.md). The engine backlog stays in
> [../FUTURE_IMPROVEMENTS.md](../FUTURE_IMPROVEMENTS.md).

---

## 0. The three papers, in one line each

| # | Paper | Domain | What we take from it |
|---|-------|--------|----------------------|
| 1 | Zou et al. (2026), *A BIM-Based Workflow for Early-Stage Embodied Carbon Assessment Using Reusable Assembly Templates and Rule-Based Mapping*, **Buildings 16(710)** | Revit add-in, carbon **accounting** (not matching) | The **certifiable packaging**: externalised auditable rule-mapping, data provenance, mismatch logging, time-to-result + coverage metrics, single transparent case study |
| 2 | Berglund-Brown (2023), *Structural Steel Reuse as a Cost-Effective Carbon Mitigation Strategy*, **MIT MS thesis** | Market + LCA of US steel reuse | Market sizing (US heavy-section scrap ≈ **140%** of import demand), cut-off LCA (**~87%** carbon cut vs recycling), and the **carbon driver ranking: weight > #elements > transport distance** |
| 3 | Metinal & Ayalp (2025), *Uncovering Barriers to Circular Construction*, **Sustainability 17(1381)** | Scientometric review | Framing only: the **41 barriers** (uncertain supply, trust/verification, fragmented data) that a certifiable tool exists to lower |

Key realisation: **Paper #1 is a carbon-accounting workflow, not a matcher.** This tool already does
the harder thing (verified donor→slot optimisation). So we don't copy its *function* — we copy its
*discipline*: the things that made a single-case Revit prototype read as complete and trustworthy.

---

## 1. What the tool already is (honest baseline)

A **member-level pre-feasibility** matcher. It ingests an extracted donor inventory and a new-design
demand model, keeps only donor→slot assignments that pass the **full EN 1993-1-1** member check, and
picks the assignment set that maximises **honest net CO₂ saved** via a MILP. Runs in plain CPython —
Revit/web/CLI are all front-ends.

| Capability | Where | Note |
|------------|-------|------|
| Full EN 1993-1-1 member checks | [`core/ec3_checks.py`](../src/steelreuse/core/ec3_checks.py) | Classification, tension, compression+buckling, biaxial bending + LTB (χ_LT), shear, 6.3.3 interaction |
| MILP carbon-optimal matching | [`match/optimize.py`](../src/steelreuse/match/optimize.py) | Sparse feasibility mask → CBC MILP; greedy fallback |
| Honest avoided-new baseline | `baseline_new_mass_kg` / `lightest_adequate_section` | Credits the *lightest adequate* new section, never the over-spec donor's mass |
| Independent optimality certificate | `verify_match` | Re-derives every cell, checks constraints + "no improving single move" |
| Binding-constraint diagnosis | `diagnose_match` | Classifies each unfilled slot (length / capacity / contention / economics) + plain-English lever |
| Pre-demolition audit | [`core/audit.py`](../src/steelreuse/core/audit.py) | Per-member condition → f_y knockdown + quarantine of unverified stock |
| Carbon passport | [`core/carbon.py`](../src/steelreuse/core/carbon.py) | A1–A3 vs reuse-process, net saved (ICE v3.0) |

**Two things almost no competitor has, and that we should lead with:**

1. **Full EN 1993 member verification** — not depth/width/weight section-swap, but the actual code
   checks across the load-combination envelope, with the governing combination reported.
2. **An independent optimality certificate** (`verify_match`) — a second, separate re-derivation that
   says *this assignment is feasible and no single move improves it*. That is the seed of "certifiable".

---

## 2. Insights from Paper #1 — what to adopt (the certifiable packaging)

Paper #1's prototype was judged complete because it was **transparent, measured, and reproducible**,
not because it was clever. Each of its methods maps to a concrete, small change here:

| Paper #1 method | What it means here | Status |
|-----------------|--------------------|--------|
| **Configurable, externalised rule tables** | Section-name mapping + carbon-factor + grade selection become a *user-inspectable, version-stamped* rule set, not implicit code | Mapping logic exists in [`core/sections.py`](../src/steelreuse/core/sections.py); needs externalising + versioning |
| **Mismatch / failure logging** | Every unmapped section, quarantined donor, infeasible pair logged *with reason* as an auditable artifact | Partly in `diagnose_match`; surface as an export |
| **Unit normalization + data provenance** | Stamp factor source/version (ICE v3.0), catalogue version, National Annex into every run | Partly present; make it a header on every output |
| **Assembly templates as reusable units** | Connection/member templates carrying their own carbon + check assumptions | Aligns with [`core/connections.py`](../src/steelreuse/core/connections.py) |
| **Time-to-result metric** (manual 290–380 min → <30 min) | Benchmark engine runtime as a *publishable* number | New — cheap to add |
| **Coverage metric** (82% rule mapping) | Report % slots matched / % donor sections mapped per run | New — cheap to add |
| **Single transparent case study** | Mirror their LoD-300 single-building validation structure | Repo has [`CASE_STUDY.md`](CASE_STUDY.md); align framing |

The throughline: Paper #1 turned *"trust me"* into *"here is the rule, the version, the coverage, the
time, and the case"*. That is exactly the gap between a clever demo and a certifiable tool.

---

## 3. "Certifiable" — concrete definition for this tool

Three layers (the ones selected), plus the third-party explainer:

1. **Reproducible audit trail.** Every reported number re-derivable from logged inputs + weights +
   catalogue/factor versions, with the `verify_match` certificate emitted per run. *Mostly exists —
   make it a first-class, single-file export.*
2. **Auditable rules + provenance.** The §2 externalised, versioned rule-mapping and stamped data
   sources. *A reviewer can see and challenge every mapping/factor.*
3. **Per-member Eurocode pass-evidence.** Attach the governing EN 1993 check to each assignment —
   clause, utilisation, χ_LT, governing load combination — as design-justification evidence.

### What "third-party / notified-body certification" actually means (explained, not built)

You asked what the third-party route is. For *reused* structural steel, no single button grants a
certificate; certification is a **chain of recognised evidence** that a competent person — and, where
formal market access is needed, a notified body — signs off:

- **SCI P427, *Structural Steel Reuse*** (UK protocol) — the de-facto reference for how to assess,
  test and document reclaimed steel for re-use. It defines condition assessment, the testing regime
  to re-establish properties, and traceability requirements.
- **EN 10025 property re-establishment** — for reclaimed members without trustworthy mill certs, the
  steel grade (f_y, f_u, toughness) is re-established by **coupon testing**, statistically (cf.
  EN 1990 Annex D) where enough samples exist.
- **EN 1090 (execution) + CE marking** — fabrication/erection of the *new* structure that incorporates
  reused members still runs through EN 1090 execution classes; CE marking applies to the fabricated
  product, and a **notified body** is involved in the factory production control certification.
- **BS 8001** — the circular-economy framework that gives the whole reuse decision an auditable,
  organisation-level governance wrapper.

**Where this tool sits:** it cannot *issue* any of these. It can produce the **evidence package** an
engineer/notified body needs — per-member EN check, audit-basis, provenance, certificate — so the
sign-off is a review of documented evidence, not a re-derivation from scratch. This is consistent with
the README's existing honesty line ("decision-support, not code-certified"). The honest claim is:
**"certifiable-ready evidence," not "certified."**

---

## 4. Competitor & startup analysis (live-searched, June 2026)

The field splits cleanly into **inventory/marketplaces** (find and list stock) and
**matchers/optimisers** (decide what fits where). This tool is firmly in the second camp.

| Name | Type | Tech approach | Business model | Open? | How it differs from this tool |
|------|------|---------------|----------------|-------|-------------------------------|
| **ēfestos / R.E.A.C.T.** | Matcher + inventory | Multimodal AI inventory from IFC/BIM/photos/mill-certs/NDT; condition scoring; **multi-dimensional Pareto cutting-stock optimiser**; plug-in to structural software | Startup + EU **MSCA** research grant (Univ. Birmingham coord., ēfestos Hub partner, 2025–2028) | No | Closest technical twin on optimisation; adds AI condition scoring this tool deliberately leaves to the PDA. No open EN-verification certificate |
| **WSP Steel Reuse Tool** | Matcher | Python, **Revit-integrated**, parametric section matching, **~90% length utilisation** (cutting-stock), Excel carbon summary | In-house consultancy IP | No | Proven on real jobs (Elephant & Castle: 74 t reused, 125 t CO₂e). Section-geometry matching, not full member verification; closed |
| **FerrousWheel** | Matcher | **Free Revit plug-in** for steel reuse, cuts embodied carbon | Free plug-in | Free (not necessarily open-source) | The closest *free* analogue — the direct comparison reviewers will reach for. Revit-bound; no documented EN certificate/audit trail |
| **RESTOR** | Early-design optimiser | Generative design + ML reuse scenarios at concept stage | Academic (Birmingham + Cambridge + Chetwoods) | No | Operates earlier in design (generative), not member-level verification of a fixed demand model |
| **Re-Bridge** | Marketplace | Shared catalogue/marketplace for reclaimed bridge steel | Platform (Expedition + Format Engineers) | No | Marketplace, not a matcher — complementary, a potential data source |
| **Stockmatcher (HTS)** | Procurement helper | Reclaimed-steel procurement matching | Vendor tool | No | Procurement-side; no engineering verification |
| **Loopfront** | Inventory/marketplace SaaS | AI-assisted surveying, internal/external marketplace, auto CO₂/cost/waste reporting | **SaaS**, ~0.01% of project cost; **$6M raised**, 90+ orgs (NO/SE/DE) | No | Broad materials asset management, not structural verification — a feeder, not a competitor to the matcher |
| **DISRUPT toolkit (ASBP)** | Guidance | Business cases, case studies, stakeholder guidance | Innovate-UK funded programme | Public docs | Not software — context and supply-chain framing |
| **Cleveland Steel & Tubes** | Physical stockist | Buys/sells reclaimed steel | Merchant | No | Physical supply; a real-world donor source |
| **Madaster / Concular** | Material passports / marketplace | Building material passports, EU marketplaces | SaaS | No | Passport/marketplace layer; complementary |

**Differentiation conclusion (the wedge).** Among the *matchers*, this tool is the **only open one that
combines full EN 1993 verification + an independent optimality certificate + an honest avoided-new
carbon baseline**. The big players either don't verify to code (WSP, FerrousWheel section-swap), keep
it closed (ēfestos, WSP), or sit in a different layer (Loopfront/marketplaces). That triad —
**open, verified, certifiable** — is defensible and is what neither academia nor the startups currently
offer for free.

---

## 5. Redefined scope & end-goal (the rebase)

**Keep the 1-to-1 matcher as the certifiable core.** Do not chase inventory capture, condition-AI, or a
marketplace — those are owned (ēfestos, Loopfront, Re-Bridge) and are not where this tool is
differentiated.

**Positioning statement:**

> *The open, verifiable decision-support layer that sits between a pre-demolition audit and a
> matcher/marketplace — it proves a donor→slot assignment passes Eurocode and books honest carbon, and
> emits a re-checkable certificate.*

**Near-term scope (serves both a paper and a market demo):**

1. Ship the **certifiable run export** — audit trail + per-member EN evidence + provenance (§3).
2. **Externalise + version the rule-mapping**, and log every mismatch (§2).
3. Add the **time-to-result and coverage metrics**; align [`CASE_STUDY.md`](CASE_STUDY.md) to Paper #1's
   single-case validation structure.
4. Write positioning/limitations clearly: **interop with marketplaces, don't compete with them.**

**Explicitly out of near-term scope:** stock/hubs (your call), inventory capture (ēfestos/Loopfront
own it), marketplace, connection *design* (screening only), and *issuing* certification.

**End-goal:** a publishable, reproducible, EN-verifiable **open reference implementation** for
reclaimed-steel member matching — usable as a research artifact *and* a free demo wedge that
complements the commercial marketplaces (consumes their inventory, feeds back verified assignments)
rather than fighting them.

---

## Sources

- [ēfestos AI](https://efestos.ai/) · [R.E.A.C.T. (CORDIS)](https://cordis.europa.eu/project/id/101202611)
- [WSP — digital approach to reusing steel](https://constructionmanagement.co.uk/wsps-digital-approach-to-reusing-steel-sections-and-saving-carbon/) · [WSP insight](https://www.wsp.com/en-gb/insights/reusing-steel-for-a-circular-economy)
- [FerrousWheel Revit plug-in (RIBAJ)](https://www.ribaj.com/products/steel-reuse-tool-ferrouswheel-cuts-embodied-carbon-revit-plug-in)
- [RESTOR (Cambridge)](https://cit.eng.cam.ac.uk/restor-0)
- [Re-Bridge (New Civil Engineer)](https://www.newcivilengineer.com/latest/expedition-engineering-and-format-engineers-launch-tool-to-increase-steel-reuse-in-bridge-works-01-05-2026/)
- [Stockmatcher](https://stockmatcher.co.uk/)
- [Loopfront](https://www.loopfront.com/) · [Loopfront funding](https://blog.loopfront.com/blog/secures-investment)
- [DISRUPT toolkit (ASBP)](https://asbp.org.uk/toolkit/disrupt-steel-reuse) · [DISRUPT programme](https://asbp.org.uk/disrupt)
- [Cleveland Steel & Tubes — reuse](https://cleveland-steel.com/we-want-buy-steel-reuse)
- Papers: Zou et al., *Buildings* **16**(710), 2026 · Berglund-Brown, MIT MS thesis, 2023 · Metinal & Ayalp, *Sustainability* **17**(1381), 2025
