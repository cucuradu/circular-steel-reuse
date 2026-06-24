# The whole journey: holding the engineer from "building dies" to "verified in the new frame"

> **Purpose.** The tool's real job is not "match steel" — it is to **carry the engineer through the
> entire reuse process** so reuse stays on the table at every stage where it normally falls off. This
> map is the *why and what* (not the how) of each stage: what really happens, why reuse usually fails
> there, what the tool covers today, and what to add — with the upside in plain words.
>
> Pairs with [POSITIONING.md](POSITIONING.md) (where we sit vs the market) and
> [ROADMAP_CERTIFIABLE_REUSE.md](ROADMAP_CERTIFIABLE_REUSE.md) (the build phases). This file is the
> wide-angle scope; iterate here, then push concrete builds into the roadmap.

Legend: 🟢 tool is strong here · 🟡 partial · 🔴 absent (and reuse often dies here).

---

## The lifecycle, stage by stage

| Stage | What really happens & why reuse fails here | Tool today | What to add — and the upside (why) |
|-------|--------------------------------------------|------------|------------------------------------|
| **0 — Trigger: a building dies** 🔴 | Owner kills the building on economics/obsolescence, not structure. By the time an engineer arrives, demolition is already the plan. **The reuse decision is lost before anyone competent is in the room.** | Nothing — pipeline starts after a model exists. | A **5-minute reuse pre-screen** from an IFC or even rough tonnage → "≈ X t steel, ≈ Y t CO₂ avoidable, ≈ £Z salvage." *Why:* puts reuse on the table at the decision moment. This is the **missing front door** — the highest-leverage gap in the whole map. |
| **1 — Feasibility / pre-redevelopment audit** 🟡 | Desk study: is keeping/reusing viable? Old drawings lie or are missing; grade unknown (pre-1970 ≠ S275). EU Protocol now pushes this *before* permits. | Extractor → instant inventory **if a BIM model exists**. | **No-model path:** OCR/import a drawing schedule or typed member list → same inventory; auto-flag "grade unverified — test required." *Why:* most tired buildings have no clean BIM; without this the tool can't even start where reuse matters most. |
| **2 — Pre-demolition audit (PDA)** 🟢 | The mandated survey: inventory every member, hazards, waste streams, reuse potential. Legal backbone of reuse. Pain: clipboard work, photos don't tie to model members, condition is subjective. | **Your strongest stage** — stable IDs, Import Survey, in-Revit Audit Grid, condition→f_y knockdown, connection/deconstructability, recoverable length. | **Field capture** (survey on-site against the live member list, photo + GPS per member); **knockdown factors cited to SCI P427** so the number is defensible; **hole/corrosion register** feeding later checks. *Why:* turns a subjective survey into citable, member-linked evidence. |
| **3 — Decision: demolish vs deconstruct** 🟡 | Wrecking ball (cheap, → recycle) vs careful deconstruction (slow, → reuse). Reuse loses because **its upside is invisible** at the moment of choosing. #1 barrier: "no demand for salvage." | Matcher can *prove* a member is reusable — but only with a demand model in hand. | **Business-case generator:** per member, "keep = save N kg CO₂, worth £M reclaimed vs £S scrap, − deconstruction labour" → ranked keep/scrap list. *Why:* makes the invisible upside a number; directly attacks the barrier that kills reuse. *(You flagged this as done — confirm it produces the ranked keep/scrap list.)* |
| **4 — Soft strip** ⚪ | Strip non-structural (fit-out, services, cladding) to expose the frame. Not structural, but sets salvage sequencing. | None (out of structural scope). | Minor: a **deconstruction-sequence view** (which members come out first/last) from data you already hold. *Why:* helps the crew; low priority. |
| **5 — Structural deconstruction** 🟢 | Unbolt/cut the frame, ideally reverse build order. Welded/riveted = hard to recover whole; bolted = easy. Sequence matters for stability. | Modelled: deconstructability, welded/riveted → review, recoverable length after cuts. | **Recoverable-length map** per member fed straight into what the matcher can offer; (bigger lift) temporary-stability check during phased removal. *Why:* the matcher should only ever offer length that will actually survive the cut. |
| **6 — Assessment, testing, certification (SCI P427 / EN 1090)** 🟡 | **The legal gate.** Group identical members, test representatives (coupon f_y, hardness, chemistry, dims), refabricated steel gets CE-marked to EN 1090 with an inspection cert. **Without this, no engineer specifies it.** | Condition knocks down f_y — but a flat factor, not a test-derived characteristic value. | **P427 testing assistant:** auto-group identical members (you already cluster), "test n of N," ingest coupon results → statistical characteristic f_y (EN 1990 Annex D); track CE/EN 1090 cert state per member. *Why:* the certification chain is where reuse legally lives or dies, and it is **under-tooled industry-wide — strong thesis white-space.** |
| **7 — Storage / stockist / inventory** 🟡(parked) | Recovered steel waits in a yard for a buyer. Dwell time = cost + risk. UK stockists now do this as real business. | Hub-stock experiment branch — parked (out of current scope by your call). | *(Deferred — stock/hub.)* When revived: persistent inventory + dwell-time field + searchable bank. *Why:* the bridge from one-off reuse to a real material bank. |
| **8 — Finding buyers / marketplace** 🔴 | Match recovered stock to a new project. Today: phone calls + a few nascent marketplaces, mostly luck. **The two sides never meet; marketplaces list stock but can't say if a beam actually works in your frame** — that structural-feasibility gap is what nobody fills. | Matcher does **one-donor → one-demand** with full EN 1993 + carbon. Crown jewel — but single-donor. | **Residual-stock export** (unmatched donors → next project's supply); **marketplace listing export** (CCH/CirCoFin schema); **multi-donor pool matching**. *Why:* positions your engine as the **structural-feasibility layer a material bank is missing** — the biggest white space. *(Pool/marketplace = deferred per scope; residual-export is in-scope today.)* |
| **9 — Refabrication** 🟡 | Cut to length, drill holes, end-prep, blast/paint, re-CE-mark. Pain: **no cut-list comes out of the reuse decision** — fabricator re-derives by hand. | You hold cut positions (cutting-stock) + connection notes. | **Cut-list / fab-output generator** per matched donor (cut lengths, where to cut, holes to drill/avoid, end-prep). *Why:* hand the fabricator a sheet, not a puzzle — removes a real adoption friction. |
| **10 — Design-in: specify & verify in the new project** 🟢 | New engineer specifies the reclaimed member and must prove it passes EN 1993 in its new role. Pain: "can I trust this old beam in my frame?" | **Core strength** — full EN 1993-1-1, frame analysis, SAP2000 parity, Revit write-back. | **Per-assignment calc-sheet drill-down** (every combo, clause, number) → "trust me" becomes "verify me"; plus **net-section-at-holes and corrosion-loss** checks. *Why:* the reuse-specific member checks nobody else does — your defensible depth. |
| **11 — Transport / logistics** 🔴 | Steel moves donor→yard→fab→site; each leg = cost + carbon. A far donor can wipe out the carbon saving. | Nothing — avoided CO₂ ignores transport entirely. | **A4 transport:** distance × mode factor → into passport + optimiser net figure, with a max-radius cutoff. *Why:* standalone-valuable, and the **prerequisite that makes multi-donor non-arbitrary** — without transport cost, choosing between donors is guesswork. |
| **12 — Documentation, passport, traceability, handover** 🟢 | Prove provenance end-to-end: from there, tested thus, refabricated so, installed here. Pain: provenance breaks between stages; certs rot in PDF inboxes. | Carbon passport per member, run history, Trace/Compare in Revit. Strong spine. | **Passport ID / QR per member** linking physical steel ↔ digital record; **Madaster/EPD-format export**. *Why:* the digital thread is what makes the whole chain auditable — and is your natural moat. |

---

## Gaps in the map — more stages worth holding the engineer through

Your 0–12 covers the *physical* journey well. These are the stages that sit **alongside** it and are
where reuse quietly dies for non-structural reasons — worth at least a flag, even if the tool only
*surfaces* them rather than solving them.

| Missing stage | Why it matters (reality) | What the tool can realistically do — and the upside |
|---------------|--------------------------|-----------------------------------------------------|
| **A — Procurement & brief** | If reuse isn't written into the client brief and the contract early, it never happens — the contractor has no mandate. Most reuse dies in a Word document, not on site. | Output a **one-page "reuse intent" pack** (Stage 0 numbers + the keep/scrap case) the client can paste into the brief/tender. *Why:* gets reuse contractually mandated before momentum sets toward demolition. |
| **B — Liability, insurance & warranty** | Engineers won't specify reused steel their PI insurer won't cover; someone must carry the declaration-of-conformity duty. **Liability fear is a top-3 barrier** in every survey. | Make the **evidence package** (roadmap 1.1) explicitly the document an insurer/PI reviewer signs against; map it to SCI P427 headings (roadmap 4.1). *Why:* you don't remove the liability, you make it *assessable* — that's what unblocks the engineer. |
| **C — Cost / QS axis** | Reuse competes on cost at Stages 3, 6, 8, 9, 11. A carbon number alone doesn't win the argument; a QS needs £. | A **cost layer** alongside CO₂ (reclaimed + refab + transport vs new) as a second optimiser axis. *Why:* turns a green argument into a business argument — the one that actually decides. |
| **D — Programme / lead-time risk** | Testing + sourcing + refab add schedule risk; PMs kill reuse to protect the programme. | Surface **lead-time per stage** (test turnaround, sourcing radius from Stage 11) as a risk flag on each assignment. *Why:* makes the time risk visible and bounded instead of a vague fear. |
| **E — Building-control / regulatory sign-off** | The approval authority must accept reused steel; precedent is thin and varies by region. | Bundle the EN-evidence + cert state into a **building-control submission view**. *Why:* a clean, standard evidence pack lowers the approver's risk and builds precedent. |
| **F — Green-rating & carbon reporting** | The carbon saving only "counts" if reported in the right standard (EN 15978/15804 module D) and toward BREEAM/LEED credits. | **EN 15978 module labelling** (A1–A3, A4, D) + an uncertainty band on the headline (roadmap 4.2). *Why:* lets the saving earn rating credits — a concrete commercial pull, not just a feel-good number. |
| **G — Design for the next reuse (loop forward)** | The new structure should itself be deconstructable, or you've spent reuse carbon on a future landfill. | **Design-for-disassembly hints** for the new frame (bolted over welded). *Why:* closes the loop forward — a stronger circular story than one-way reuse. |
| **H — Common data thread / interoperability** | The chain breaks because every stage uses a different format; data and certs don't survive handover. | One **versioned schema** carried across stages (extends roadmap 1.2/3.1). *Why:* the digital thread *is* the product — it's what no marketplace or stockist currently owns. |

---

## What this map tells you (the conclusion)

The tool is **deep in the middle and thin at the ends** — and the ends are exactly where reuse fails
in the real world:

- **Deep (🟢):** Stage 2 (PDA), Stage 5 (deconstruction modelling), Stage 10 (EN verification),
  Stage 12 (passport). This is real, defensible engineering depth.
- **Thin / absent (🔴), and where reuse actually dies:** Stage 0 (no front door), Stage 1 (no
  no-model path), Stage 3 (upside invisible — *you may have closed this*), Stage 8 (buyers never
  meet feasibility), Stage 11 (transport ignored).

**Strategic read:** the cheapest, highest-leverage moves are at the **front** (Stage 0 pre-screen,
Stage 3 business case) and the **carbon-honesty** end (Stage 11 transport), because they decide
*whether reuse happens at all* — the verification depth in the middle only pays off if a member ever
reaches it. The certifiable evidence work in [ROADMAP_CERTIFIABLE_REUSE.md](ROADMAP_CERTIFIABLE_REUSE.md)
is what makes the deep middle *trusted*; this map is what makes sure members arrive there.

> **Scope reminder.** Stages 7 (storage) and 8's pool/marketplace pieces are **deferred** (stock/hub —
> your call). Everything else above is on-scope for an engineer-guidance tool and maps to existing
> backlog items in [../FUTURE_IMPROVEMENTS.md](../FUTURE_IMPROVEMENTS.md).
