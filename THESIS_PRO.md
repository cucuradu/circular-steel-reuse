# Circular Structural Reuse Matcher

### A Decision‑Support Workflow for Eurocode‑Compliant Steel Reuse and Embodied‑Carbon Quantification

---

**Master's Thesis**

**Author:** [Your Full Name]  ·  **Student ID:** [ID]<br>
**Degree programme:** [M.Sc. in Civil Engineering — Structural Engineering]<br>
**Institution:** Politecnico di Milano<br>
**Supervisor:** [Supervisor]  ·  **Co‑supervisor:** [Co‑supervisor]<br>
**Academic year:** [20XX–20XX]

> Bracketed fields are placeholders to be completed before submission.

---

## Abstract

Embodied carbon — the greenhouse‑gas emissions of producing and constructing a building's materials —
is now a decisive share of a structure's lifetime impact, and structural steel is a principal
contributor: producing one kilogram releases on the order of 1.55 kgCO₂e. Conventional end‑of‑life
recycling re‑melts steel and discards the embodied value of its rolled shape, fabrication and
certification. Direct reuse of reclaimed members avoids almost all of that burden, but identifying which
reclaimed members can safely occupy which positions in a new design — across thousands of candidate
pairings, each subject to the full set of code checks — is intractable by hand.

This thesis presents a software workflow that automates the assessment. Given a digital model of a
building to be deconstructed (the *donor*) and of a new design (the *demand*), it identifies the steel
sections, derives the design actions, verifies every candidate member against Eurocode EN 1993‑1‑1,
quantifies the embodied carbon avoided against a defensible baseline, and solves a Mixed‑Integer Linear
Program to obtain the carbon‑optimal feasible assignment. An optional global frame analysis supplies
realistic action effects (gravity load path, sway imperfection, wind, seismic, and second‑order effects).
A language model generates the report prose under a strict constraint that it performs no arithmetic; all
quantities are computed deterministically and validated. The deterministic core is hand‑verified against
published section data and protected by 127 automated tests. The tool is scoped as member‑level
pre‑feasibility decision support: it does not design connections, and reclaimed material requires physical
verification before reliance.

**Keywords:** circular economy; steel reuse; embodied carbon; Eurocode EN 1993‑1‑1; BIM; Mixed‑Integer
Linear Programming; design for deconstruction.

---

## Contents

1. Introduction
2. Background
3. System architecture and design principles
4. Model ingestion and section identification
5. Actions and internal forces
6. Member verification (EN 1993‑1‑1)
7. Global frame analysis
8. Embodied‑carbon accounting
9. Optimal matching
10. Reporting, narrative generation, and the machine‑learning study
11. Verification and validation
12. Results
13. Limitations and future work
14. Conclusion
- References · Appendix A Notation · Appendix B Command‑line interface · Appendix C Assumptions register

---

# 1. Introduction

## 1.1 Context

Operational carbon is falling as buildings become more efficient and grids decarbonise, which raises the
relative weight of *embodied* carbon — the emissions incurred before occupation. Structural frames
dominate that figure, and steel in particular: its cradle‑to‑gate production (life‑cycle stages A1–A3)
emits roughly 1.55 kgCO₂e per kilogram. The prevailing circular strategy for steel is recycling, which,
although efficient, re‑melts the material and forfeits the energy and value already embodied in the rolled
profile. **Direct reuse** — recovering, verifying and re‑erecting a member essentially as‑is — retains
that value and avoids almost all production carbon, incurring only a small recovery and refabrication
cost.

## 1.2 Problem statement

Direct reuse is gated by a matching problem that is awkward to solve manually for four reasons: the number
of candidate (member, position) pairings is large; each pairing must satisfy the complete set of code
checks for the actions at that position; among the feasible assignments, those that maximise carbon saving
differ materially from arbitrary feasible ones; and the source data (BIM section names, material grades)
is inconsistent and incomplete. A practical tool must therefore combine reliable section identification,
correct code verification, defensible carbon accounting, and optimisation, while remaining transparent
about its assumptions.

## 1.3 Aim and objectives

The aim is a workflow that, from a donor and a demand model, proposes a structurally valid, carbon‑optimal
assignment of reclaimed members to new positions and quantifies the saving. The objectives are to (O1)
ingest steel members from BIM (Autodesk Revit and IFC); (O2) identify sections without unsupported
guessing; (O3) derive design actions per recognised load rules; (O4) implement the EN 1993‑1‑1 member
checks as the deterministic source of truth; (O5) account for embodied carbon against the correct
baseline; (O6) formulate and solve the matching as a carbon‑maximising program; (O7) report results with
language‑model prose but deterministic numbers; and (O8) document scope and assumptions explicitly.

## 1.4 Scope and limitations

The tool performs **member‑level pre‑feasibility screening**; its results are decision support, not
code‑certified design. It does not design connections (bolts, welds, plates), which frequently govern the
practicality of reuse; it does not certify reclaimed material, which requires coupon testing for grade and
survey for corrosion and fatigue; and it does not substitute for an engineer's judgement or a full
code‑compliant design. These boundaries are intrinsic to the contribution: making them explicit is part of
the method (Chapter 13 gives the complete limitation register).

## 1.5 Outline

Chapter 2 establishes the necessary background. Chapter 3 presents the architecture and the governing
design principles. Chapters 4–10 detail the pipeline in execution order. Chapter 11 covers verification
and validation, Chapter 12 a worked result, Chapter 13 the limitations and roadmap, and Chapter 14 the
conclusion.

---

# 2. Background

## 2.1 Embodied carbon

Environmental performance is assessed over the life‑cycle stages of EN 15978. The relevant quantities here
are the cradle‑to‑gate production of new steel (A1–A3 = 1.55 kgCO₂e/kg) and the process cost of preparing
a reclaimed member for reuse (clean, test, refabricate = 0.10 kgCO₂e/kg). Reuse therefore avoids
approximately 1.55 − 0.10 = 1.45 kgCO₂e per kilogram relative to new procurement.

[[FIG-CARBON]]

## 2.2 Steel sections

Hot‑rolled sections are catalogued shapes with standardised geometric properties. The tool covers
doubly‑symmetric open I/H profiles — the European IPE and HEA/HEB/HEM series and the American AISC W
series — and the closed rectangular/square AISC HSS (hollow structural sections), each verified with the
rules appropriate to its shape family. Each section is characterised by its overall depth `h`, flange
width `b`, web and flange thicknesses `t_w` and `t_f` (a hollow section has one uniform wall `t`), root
radius `r`, area `A`, mass per metre, second moments of area `I_y`/`I_z`, elastic and plastic section
moduli `W_el`/`W_pl`, and radii of gyration `i_y`/`i_z`. The major (strong) axis is `y`
and the minor (weak) axis is `z`; bending resistance and buckling behaviour differ greatly between them.

[[FIG-SECTION]]

## 2.3 Limit‑state verification

EN 1993‑1‑1 verifies a member against several failure modes, each expressed as a *utilisation*, the ratio
of the design action effect to the corresponding resistance; the member is adequate when every applicable
utilisation does not exceed unity, and the governing check is the maximum. The modes are yielding/tension,
compression with flexural buckling, bending with lateral‑torsional buckling (LTB), shear, their
interaction, and the serviceability deflection limit. EN 1990 supplies the partial factors and load
combinations, EN 1991 the action magnitudes, and EN 1998 the seismic provisions.

[[FIG-FAILURE]]

## 2.4 BIM data sources

A building is modelled as a set of objects with geometric and material attributes. The workflow reads
**Autodesk Revit** (via the open‑source pyRevit add‑in, which exports the model from inside Revit) and
open **IFC** files (via IfcOpenShell), normalising both to a single intermediate representation.

---

# 3. System architecture and design principles

The workflow is a pipeline of independent stages, each separately testable, communicating through a
standard intermediate format. Two building models are reduced to JSON by the extractor; the remaining
stages run in standard CPython.

[[FIG-PIPELINE]]

Five principles, enforced throughout the codebase, give the tool its credibility:

1. **The language model performs no arithmetic.** Every quantity is computed in inspectable Python and
   injected into the report; the model writes only the surrounding prose, and an automatic check rejects
   any output that alters or introduces a figure.
2. **Heavy computation runs outside Revit.** The in‑Revit extractor only reads the model and writes JSON,
   depending on nothing beyond the standard library.
3. **The deterministic EN 1993‑1‑1 check is the single source of truth.** Machine‑learning components are
   never authoritative.
4. **Unknown structural assumptions default to the conservative choice and are flagged**, never silently
   favourable.
5. **Section identity is never guessed silently.** Unresolved names are reported in an explicit *unknown*
   category.

---

# 4. Model ingestion and section identification

## 4.1 Extraction and schema

The extractor traverses the structural framing and columns and records, per member, an identifier, role
(beam/column/brace), the original ("raw") section name, length, end coordinates and any material grade.
Donor members carry a single physical `length` — the reusable stock length — whereas demand members carry
`spans`, the structural spans after subdivision at supports; this distinction governs later length
feasibility. All quantities are normalised to newtons and millimetres at ingestion, so stresses are in
MPa throughout.

## 4.2 Section mapping

Raw names are resolved to catalogue sections through an ordered procedure that stops at the first
confident result: exact match; user override (a supplied CSV); normalisation (whitespace, leading zeros,
ordering — e.g. "HE 300 A" → "HEA300"; the trailing AISC designation extracted from a verbose family
string, e.g. "W Shapes‑Column W14x109" → "W14X109"); fuzzy similarity; otherwise *unknown*. Fuzzy matches
(for example IPE300 versus IPE330, ~0.83 similarity) are **quarantined**: reported but excluded from
analysis until confirmed, since substituting near‑neighbour properties would corrupt the checks. A
validation report summarises the mapped, fuzzy and unknown counts.

When the extractor has captured the member's **measured section dimensions** (depth, flange width,
flange and web thickness — read from the Revit type's structural‑section parameters, or from the IFC
I‑shaped profile definition), a fuzzy or unknown *name* can additionally be confirmed by **physical
dimensions**: if every captured dimension matches exactly one catalogue row within a tight tolerance
(`max(1 mm, 1.5 %)`, far below the step between adjacent catalogue sizes), the member is identified by
geometry (method `geometry`) rather than left to manual confirmation. A fuzzy name requires depth and
width; a name with no signal at all requires all four dimensions. Ambiguity confirms nothing — this is
identification by measurement, never a guess.

## 4.3 Catalogues and grades

The bundled catalogue holds 711 sections: 40 European (IPE 160–600; HEA/HEB/HEM 200–400), 283 AISC
W‑shapes, and 388 AISC rectangular/square HSS — the US tables stored verbatim in imperial units from the
AISC Shapes Database v15.0 and converted on load, so the published values remain auditable. Each section
is tagged by standard (EU/US) and shape family. The verification layer is **shape‑aware**: I/H/W shapes
use the EN 1993‑1‑1 open‑section rules, while hollow sections classify every wall as an internal part
(`c = h − 3t`), take the cold‑formed buckling curve c on both axes, use the RHS shear area
`A_v = A·h/(b+h)`, and are exempt from lateral‑torsional buckling (a closed section's torsional stiffness
keeps the LTB slenderness below the plateau at any practical span). Mono‑symmetric shapes — channels and
angles — remain deliberately in the unknown category rather than be checked with inapplicable formulae;
round tube likewise awaits a `D/t` classification rule. Members without a grade — common in US models —
receive the standard grade for their shape family (e.g. W → A992, 345 N/mm²; HSS → A500) rather than the
weaker European default, and the assumption is recorded. A property‑consistency test re‑derives mass,
moduli and radii from primary dimensions for all 711 rows (HSS on the AISC nominal‑weight/design‑wall
basis, `t_des = 0.93·t_nom`).

## 4.4 Pre‑demolition audit and provenance

The donor inventory is, in regulatory terms, the deliverable of a **pre‑demolition audit**: the survey
conducted before demolition or deep refurbishment that records, member by member, the quantity, the
physical condition, and the basis on which the steel grade can be trusted. The audit is increasingly
mandated or recommended — the EU Construction & Demolition Waste Management Protocol, France's
*Diagnostic PEMD*, the EU *Level(s)* framework, and Italy's *CAM Edilizia* — and reuse‑specific guidance
(SCI P427) permits reliance on reclaimed steel only where its grade is established by mill certificate or
coupon test and its condition is sound. Where §4.1's inventory answers *how much*, the audit answers
*how trustworthy*.

The tool represents the two audit facts as fields on each donor member — a condition grade (A–D) and a
verification basis (mill certificate, coupon test, documentary, visual, or unverified) — supplied in the
model file or merged from an auditor's CSV (`--pda`). It converts them into the two quantities the
verification already understands: a per‑member knockdown on the yield strength, taken as the product of a
condition factor and a verification factor (or an explicit value the auditor sets directly), and a
quarantine decision that removes unverified or unsuitable (condition D) members from the certified supply
in the same way a fuzzy section match is withheld. A recoverable length captures the usable stock after
de‑construction. The design is honest by default: a member carrying no audit data is treated as legacy
input and admitted at the run's default knockdown, so the feature never alters a result that was not
audited — absence of data is read as "not audited", not as "sound". The provenance then surfaces in the
material passport (§8.1), in a dedicated audit section and per‑assignment column of the report (§10), and
on the console, making the audit an explicit, traceable input rather than a silent assumption.

---

# 5. Actions and internal forces

## 5.1 Area load model

Design loads follow the pre‑sizing convention of a floor‑area pressure with explicit EN 1990 factors. A
characteristic permanent load `g_k` (default 3.5 kN/m²) and imposed load `q_k` (default 3.0 kN/m²) give
the ultimate design pressure `p_Ed = γ_G g_k + γ_Q q_k = 1.35·3.5 + 1.5·3.0 = 9.225 kN/m²`; the
unfactored `g_k + q_k = 6.5 kN/m²` is retained for the deflection check. Each member carries its
*tributary* share of the floor: a beam a line load `w_Ed = p_Ed · b_trib`, a column an axial force
`N_Ed = p_Ed · A_trib · n_floors`. Tributary widths and areas are either configured defaults or estimated
from the model geometry — half the spacing to the nearest parallel framing on each side, with an edge
member conservatively taking the full bay, and a column's floor count taken from its vertical stack.

[[FIG-TRIB]]

## 5.2 Internal forces

For a simply‑supported span of length `L` under uniform load `w`, the design effects are `M = wL²/8` and
`V = wL/2`; a column receives an axial demand over its full length, taken as the buckling length. An
optional finite‑element backend (PyNiteFEA) reproduces these closed‑form results for a determinate span,
an equivalence enforced by test.

[[FIG-BEAM]]

## 5.3 Load‑combination envelope

A member is verified against a list of design situations and must satisfy every one; the governing
(worst‑utilisation) situation is reported. The default envelope is the gravity case plus, optionally for
columns, the EN 1993‑1‑1 §5.3.2 global sway imperfection applied as a notional moment `M = N·φ·L` (the EN
value φ = 1/200; disabled by default so baseline results are unchanged). An opt‑in **construction‑stage
case** (`--construction`) adds, for every beam, the bare‑steel erection situation: full permanent load
(the wet slab is on the beam) plus the EN 1991‑1‑6 construction live load (default 0.75 kN/m²), with the
compression flange **unrestrained** — the slab that justifies `χ_LT = 1` in the persistent case does not
yet exist, so the lateral‑torsional reduction applies in earnest. A beam that passes only by virtue of
slab restraint is thereby caught as a hard check, not merely flagged. Reuse feasibility and the
avoided‑new baseline (§8.2) both require passing the entire envelope. Additional situations append to this
list, which is the mechanism by which the frame analysis introduces wind and seismic.

---

# 6. Member verification (EN 1993‑1‑1)

The deterministic checks (constants `E = 210 000`, `G = 80 769 N/mm²`, `γ_M0 = γ_M1 = 1.0`; `N_Ed`
compression‑positive) are the source of truth; every other component consumes their output.

## 6.1 Material and classification

The nominal yield `f_y` follows Table 3.1, including the thickness reduction for European grades
(e.g. S355 → 335 N/mm² for `40 < t_f ≤ 80 mm`); ASTM grades carry a single specified minimum. The material
factor is `ε = √(235/f_y)`. Cross‑sections are classified (Table 5.2) from the flange‑outstand ratio
`c/t_f` against `9ε/10ε/14ε` and the web ratio `c/t_w` against `33ε/38ε/42ε` (compression) or
`72ε/83ε/124ε` (bending); the section class is the worse of the two, with the conservative compression
limits used under combined actions. Class 4 (slender) sections fall back to the elastic modulus, are
marked for review, and are flagged, as effective‑section design is out of scope.

## 6.2 Cross‑section resistances

Tension and compression resistances are `A f_y/γ_M0`; bending resistance is `W_pl f_y` (class 1–2) or
`W_el f_y` (class 3); shear resistance is `A_v (f_y/√3)/γ_M0`, with `A_v` the web shear area.

## 6.3 Flexural buckling (6.3.1)

The Euler critical load `N_cr = π²EI/L_cr²` and relative slenderness `λ̄ = √(A f_y/N_cr)` give the
reduction factor `χ = 1/(φ + √(φ² − λ̄²))` with `φ = 0.5[1 + α(λ̄ − 0.2) + λ̄²]` and `χ = 1` for `λ̄ ≤ 0.2`.
The imperfection factor `α` is selected from the Table 6.2 buckling curve, chosen from `h/b` and the
flange thickness (thicker flanges shift to a less favourable curve, correctly handling heavy W‑shapes).
Compression is governed by the weaker axis; buckling‑length factors are `k = 1.0` (pinned) by default.

## 6.4 Lateral‑torsional buckling (6.3.2)

The torsion and warping constants are derived from geometry, `I_t = (2 b t_f³ + (h − 2t_f) t_w³)/3` and
`I_w = I_z (h − t_f)²/4`; both under‑predict the critical moment, so the result is conservative. The
elastic critical moment is `M_cr = C₁ (π²E I_z/L²)·√(I_w/I_z + L²G I_t/(π²E I_z))`, giving
`λ̄_LT = √(W_y f_y/M_cr)` and the rolled‑section reduction `χ_LT` (with `λ̄_LT,0 = 0.4`, `β = 0.75`,
`α_LT = 0.34` or `0.49`; `C₁ = 1.0`, uniform moment). A compression flange restrained by a slab sets
`χ_LT = 1`; the unrestrained value is nonetheless computed and a warning is raised when it is low, exposing
reliance on the slab (notably at the construction stage).

## 6.5 Interaction, deflection, knockdown

Combined axial force and bending are verified with the **full clause 6.3.3 beam‑column interaction**,
equations (6.61) and (6.62), with the Annex B (Method 2) interaction factors: Table B.1 for class 1–2
(including the RHS variant of `k_zz`), Table B.2 for class 3, and the susceptible/not‑susceptible
`k_zy` split — a slab‑restrained flange or a hollow section is treated as not susceptible to torsional
deformation. All equivalent‑moment factors are held at `C_m = 1.0`, the Table B.3 upper bound, so the
factors remain conservative for any real moment shape; `χ_LT` multiplies `M_y,Rk` exactly as the code
equations prescribe, so lateral‑torsional buckling can never be bypassed in a beam‑column. The check is
**biaxial**: minor‑axis moments from lateral or sway frame cases enter through `k_yz`/`k_zz`, a
minor‑axis‑only moment is checked against `M_z,Rd` (no LTB about z), and biaxial bending without axial
uses the always‑conservative linear cross‑section sum of cl. 6.2.1(7). The implementation is validated
against a hand‑computed IPE300 beam‑column chain (§11). The serviceability check limits the simply‑supported deflection
`δ = 5wL⁴/(384 E I_y)` to `L/250` under the characteristic load. A reclaimed‑steel knockdown (≤ 1.0)
optionally reduces `f_y` to reflect material uncertainty and is always flagged; the default of 1.0 assumes
the grade is confirmed by testing. The member status is FAIL (utilisation > 1), REVIEW (class 4) or OK.

---

# 7. Global frame analysis

By default each member is verified in isolation, with column axials taken from the tributary estimate. The
optional analysis (`--frame-analysis`, PyNiteFEA) instead assembles and solves the demand structure,
producing the same per‑member action‑effect envelope from a connected model; the isolated path remains the
default and the fallback.

## 7.1 Topology and idealisation

Endpoints within a tolerance are snapped into shared nodes; column feet at the lowest level become
supports; members lacking usable coordinates revert to the isolated path. The idealisation is a **simple
braced frame**: pinned beam‑to‑column connections (beams remain simply supported, recovering `wL²/8`),
continuous columns, and fixed bases, with lateral load carried by explicit braces or, absent bracing, by
the bases.

## 7.2 Load path

The floor pressure is applied to the beams only; each column axial then emerges from the solved load path.
Consequently a multi‑storey column accumulates the floors above it and an interior column collects from
both sides — superseding the tributary estimate.

[[FIG-FRAME]]

## 7.3 Lateral actions and second‑order effects

Three optional lateral actions are supported, each triggering a second‑order (P‑Δ) solve. The **sway
imperfection** (`--phi`, §5.3.2) is applied as equivalent horizontal forces `H_i = φ N_Ed` at the column
tops. **Wind** (`--wind q`, a user EN 1991‑1‑4 net pressure) is applied as storey forces
`q · b_perp · h_trib` in a wind‑leading combination (`ψ₀ = 0.7`); it requires a three‑dimensional model.
**Seismic** (`--seismic Cs`, EN 1998‑1 lateral force method) distributes a base shear `F_b = Cs·ΣW_i`
over the height in the inverted‑triangular first mode, with the base‑shear coefficient supplied by the
user. The P‑Δ pass captures sway amplification.

## 7.4 Continuous members and robustness

Continuous beams are split at interior supports so each span is checked over its own length and its
reaction is routed to the correct column. Any solver failure is caught and the run reverts to the isolated
analytic loads with a warning, never aborting.

---

# 8. Embodied‑carbon accounting

## 8.1 Material passport

Using factors from a published dataset (ICE v3: A1–A3 = 1.55, reuse process = 0.10 kgCO₂e/kg, swappable),
the tool computes per mapped member its mass, volume, new‑production carbon, reuse‑process carbon and net
saving. Summed over the donor, this is the building's total reuse potential.

## 8.2 Avoided‑new baseline

The saving credited to a reuse is measured against the **lightest catalogue section that passes the
position's checks** — the member that would otherwise have been procured — not against the reclaimed
member's own mass. This prevents a heavy donor placed in a light position from over‑crediting carbon and
removes the corresponding bias in the optimiser. The baseline is restricted to the position's own design
standard (a US position is benchmarked against a W‑shape), while reclaimed supply is unrestricted, since
cross‑standard reuse is legitimate. The net booked saving is
`baseline_mass·1.55 − reused_mass·0.10 − connection_refabrication`.

---

# 9. Optimal matching

The matcher assigns reclaimed members (supply) to design positions (slots) to maximise net carbon saving
subject to feasibility and use constraints.

[[FIG-MATCH]]

## 9.1 Feasibility and scoring

A (supply, slot) pair is admissible only if the member is long enough (`length ≥ required + 50 mm`) and
passes the exact EN 1993‑1‑1 verification for that slot's actions across every combination of the
envelope; the governing combination is recorded. Admissible pairs are scored by net carbon saving (§8.2)
less a soft off‑cut penalty that discourages consuming long stock for short demands without booking the
remainder as emitted.

## 9.1.1 Connection feasibility screen

Connections frequently govern whether a reuse is *practical*, yet connection design is outside this
tool's scope. The middle ground is a **geometric compatibility screen** between each donor and the
section the design specified for the slot — the section its connections (fin plates, end plates, seats,
splices) were detailed around. A donor of the wrong shape family (tube for an open position or vice
versa), or one standing more than 50 mm deeper than the design section, is `incompatible`: the
connection typology or the detailed zone itself would have to change. A donor markedly shallower, with
a much thinner web (bolt bearing) or a much narrower flange (seats, end plates) is flagged `review` —
connectable, but the details need an engineer's look. The screen never judges strength (that is the
EN checker's job) and a slot with no known design section yields no opinion, so absence of data never
blocks reuse. By default the screen only *annotates* every assignment (a Connection column in the
report); with `--connections` enabled, `incompatible` pairs are excluded before matching. All
tolerances are an explicit, overridable policy. Designing the connections themselves — bolts, welds,
plates — remains out of scope.

## 9.2 Optimisation and fallback

The selection is a Mixed‑Integer Linear Program (binary assignment variables, at most one supply per slot
and one slot per supply, maximising total score), solved to proven optimality by CBC via PuLP. If the
solver is unavailable or does not converge, a greedy heuristic selects highest‑scoring admissible pairs
first and, like the program, never books a carbon‑negative match.

## 9.3 Cutting‑stock

Optionally (`--cut`), one donor may be cut into several pieces bounded by its length
(`Σ(required + 50 mm) ≤ length`); the off‑cut penalty is then dropped because the remainder is genuinely
reusable, and each donor's leftover is reported. The default remains one piece per donor.

---

# 10. Reporting, narrative generation, and the machine‑learning study

All figures are computed deterministically and rendered to an HTML report (and an interactive dashboard).
A configured language model (Google Gemini, with a local Ollama fallback) writes only the explanatory
prose; a post‑generation check rejects any text containing a figure absent from the computed results,
enforcing Principle 1. The provider is interchangeable and does not affect any result.

A machine‑learning study accompanies the project but is deliberately excluded from the result path
(Principle 3). It comprises a capacity surrogate (an XGBoost model imitating the checker, whose high
reported accuracy is acknowledged as circular because its labels come from the checker itself), a
transparent reuse‑score heuristic, and section clustering. These are exploratory: integrating any of them
would require a non‑circular validation against real reuse outcomes.

---

# 11. Verification and validation

**Hand verification.** The deterministic core is checked against published IPE300 section data, including
`ε(355) = 0.814`, `N_t,Rd(S275) = 1479.5 kN`, `M_pl,Rd = 147.6/172.7 kNm` (S235/S275),
`V_pl,Rd(S235) = 348 kN`, flexural buckling `χ_z(L = 4 m, S275) = 0.392`, LTB `χ_LT(L = 6 m) ≈ 0.45`
decreasing with span, and deflection `δ ≈ 9.62 mm` (w = 10 N/mm, L = 6 m).

**Automated suite.** 127 tests (across twelve files) pass under a clean linter, covering the member
checks, the matcher (known‑answer feasibility, use constraints, the avoided‑new and standard‑restricted
baselines, degenerate‑geometry safety, the greedy guard, the combination envelope, cutting‑stock), the
frame analysis (topology, recovery of `wL²/8`, multi‑storey accumulation, sway/wind/seismic forces,
multi‑span splitting), and catalogue integrity for all 711 rows.

**Methodology record.** A methodology document maps each clause to its implementation, assumption and
validation basis; the limitation register (Chapter 13) states the explicit non‑claims.

---

# 12. Results

On a representative US donor of 1016 members, 435 map to catalogue sections (the W‑shapes plus one HSS),
the remainder (overwhelmingly open‑web bar joists, plus concrete, channels and angles) being correctly
reported as unknown; missing grades are assigned flagged defaults. The demand model is assembled into a
**global frame of 274 nodes and 492 elements** and solved, so the design forces come from the real load
path; the new design resolves to 181 steel positions, of which the optimiser fills 50 with reclaimed
members that pass every EN 1993‑1‑1 combination, saving ≈ 39.3 t CO₂e on the avoided‑new basis — reported
separately from the donor stock's ≈ 315 t total embodied carbon so the design's absorptive capacity is
visible. On a hand‑checkable two‑bay two‑storey demand, frame analysis yields an interior column of
332 kN against a corner column of 166 kN — the 2:1 ratio confirmed by hand statics — demonstrating the
load‑path effect. The case‑study run summary is:

```
Loads: area-based, 3.5+3 kN/m^2 (G+Q), ULS 1.35G+1.5Q, tributary 3 m; demand = steel only
Forces: frame analysis (PyNite) — 274 nodes, 492 members
Mapping: 435 mapped, 0 fuzzy, 581 unknown of 1016 members
Supply 435 | demand slots 181 | reused 50
CO2e saved by matches: 39264.5 kg (full donor stock potential: 315486.4 kg)
Narrative source: deterministic
```

---

# 13. Limitations and future work

Severity follows the project register: 🔴 affects credibility or correctness · 🟠 methodology gap (usually
conservative and documented) · 🟡 minor. Items marked *(out of scope)* are deliberate non‑goals.

**Real‑world feasibility (governing).**
🔴 *Connection design (out of scope):* bolts, welds and plates are not designed, yet often govern reuse.
The tool now ships a **geometric connection feasibility screen** (§9.1.1) — shape family, depth band,
web and flange compatibility against the slot's design section, annotating every assignment and
optionally excluding incompatible donors — but the screen is geometry, not capacity: connection design
and its verification remain the engineer's. 🟠 *Material certification:* the tool now
ingests a **pre‑demolition audit** (§4.4) — per‑member condition and verification basis driving a derived
knockdown and a quarantine of unverified or unsuitable stock — so grade trust is an explicit, traceable
input rather than a global figure; the survey itself (coupon‑test programme, corrosion/fatigue assessment,
weldability of old steel) remains the engineer's responsibility and out of scope. 🔴 *Not code‑certified:*
results are decision support, to be confirmed by a qualified engineer.

**Member verification.**
🟡 Combined N+M is the full 6.3.3 (Annex B Method 2) biaxial interaction, but with `C_m = 1.0` (the
conservative upper bound — no moment‑shape refinement) and member rotation about its own axis assumed
at the default orientation. 🟠 No shear–moment (6.2.8) interaction.
🟠 Effective lengths are fixed at `k = 1.0`. 🟠 Class 4 sections are flagged, not designed.
🟡 LTB uses `C₁ = 1.0` and geometry‑approximated `I_t`/`I_w` (conservative); the slab‑restraint assumption
is the one non‑conservative default, mitigated by the always‑computed unrestrained `χ_LT` warning
and the opt‑in construction‑stage case. 🟡 The construction‑stage (bare‑steel) case is opt‑in
(`--construction`) rather than always on, and uses the full permanent load with isolated‑span statics
(conservative for the casting situation, but no staged erection sequence).

**Actions.**
🟠 The member‑level envelope ships only gravity and the optional sway case; wind, seismic, pattern and
uplift combinations populate as additional entries (the frame path already provides wind and seismic).
🟠 Column moments at member level are notional only (no real moment transfer). 🟡 The tributary edge rule
assumes no overhang. 🟡 Geometry‑based load estimation is opt‑in.

**Frame analysis.**
🟠 Seismic is the simplified lateral‑force method with a user base‑shear coefficient — no modal
response‑spectrum, accidental torsion or behaviour‑factor spectrum. 🟠 `k = 1.0` as above (the solve
gives forces, not buckling lengths); biaxial column moments are now carried per axis into the 6.3.3
check. 🟡 Lateral actions are applied along the X and Y axes only. 🟠 Frame analysis requires coordinates,
which the IFC path does not yet export.

**Data and catalogue.**
🟠 *(human task)* The pyRevit extractor has run on a live Revit model (the §12 case study was
re‑extracted with full column coordinates), but the formal completeness check — member count against a
Revit structural schedule, via `steelreuse-validate --schedule` — has not been ticked, and the extraction
predates the measured‑dimension capture, so one further re‑extraction is needed for geometry
auto‑confirmation to engage on the real model. 🟠 The IFC extractor exports
no coordinates. 🟠 The catalogue omits small European sizes and the mono‑symmetric families (UB/UC,
channels, angles) plus round tube, which require further shape‑aware checks (rectangular/square HSS are
now catalogued and checked with hollow‑section rules). 🟡 Fuzzy matches without captured
dimensions still require manual confirmation; when the extractor records the measured section
dimensions, a fuzzy or unknown name is auto‑confirmed by a unique physical‑dimension match
(method `geometry`).

**Carbon and optimisation.**
🟠 Single‑objective (carbon only); cost, transport and programme are not yet traded off. 🟡 Cradle‑to‑gate
scope with one dataset (no A4/A5 or end‑of‑life). 🟡 Cutting‑stock and the cross‑standard restriction lack
opt‑in toggles for alternative behaviour.

**Machine learning and validation.**
🟡 The ML study is exploratory and unintegrated; integration needs non‑circular validation. 🟡 Validation
rests on per‑check hand calculations plus the end‑to‑end worked example (§11; one complete bay through
the whole pipeline with every stage asserted against the hand chain); the worked example is
self‑derived, so a cross‑check against an independently *published* design example remains a
worthwhile addition.

**Priority roadmap.** (1) extend the connection screen toward capacity (standard end-connection shear
tables); (2) ~~full 6.3.3 and biaxial interaction~~ **done** (§6.5); (3) ~~construction‑stage
(bare‑steel) case~~ **done** (§5.3, opt‑in `--construction`);
(4) calibrate the audit condition→knockdown factors against test data; (5) formal schedule‑count
validation + re‑extraction with measured dimensions; (6) a complete combination set (pattern, uplift)
and modal seismic; (7) shear–moment (6.2.8) interaction; (8) IFC coordinate export; (9) effective‑length
inference; (10) shape‑aware checks for channels/angles/round tube and the small European sizes;
(11) multi‑objective optimisation; (12) an independently published validation benchmark.

---

# 14. Conclusion

This thesis demonstrates that the assessment of direct structural‑steel reuse can be automated as a
transparent, Eurocode‑aware workflow that also quantifies the embodied carbon saved. From a donor and a
demand model, the tool identifies sections without unsupported guessing, derives design actions (optionally
from a global frame solve with sway, wind, seismic and second‑order effects), verifies every candidate
against the full EN 1993‑1‑1 member checks, accounts for carbon against a defensible avoided‑new baseline,
and obtains the carbon‑optimal feasible assignment by Mixed‑Integer Linear Programming. Language‑model
assistance is confined to prose, with arithmetic reserved to validated deterministic code.

The contribution is as much methodological as computational: by fixing a clear scope — member‑level
pre‑feasibility, excluding connection design and material certification — encoding conservative defaults,
hand‑verifying the engineering core against published data, and exposing every assumption, the tool is
trustworthy within its stated boundary and explicit about where that boundary lies. In doing so it lowers
the effort of recovering the carbon and economic value embodied in existing steel, and shows that rigorous
engineering and a disciplined use of automation can coexist.

---

# References

1. EN 1993‑1‑1, *Eurocode 3: Design of steel structures — Part 1‑1*. CEN.
2. EN 1990, *Eurocode — Basis of structural design*. CEN.
3. EN 1991‑1‑1 and EN 1991‑1‑4, *Eurocode 1: Actions on structures*. CEN.
4. EN 1998‑1, *Eurocode 8: Design of structures for earthquake resistance — Part 1*. CEN.
5. EN 15978, *Sustainability of construction works — Assessment of environmental performance of buildings*. CEN.
6. AISC Shapes Database, v15.0. American Institute of Steel Construction.
7. Hammond, G. & Jones, C. *Inventory of Carbon and Energy (ICE), v3* (2019).
8. PyNiteFEA — open‑source 3‑D finite‑element frame analysis (Python).
9. PuLP / CBC — open‑source MILP modelling library and solver.
10. IfcOpenShell — open‑source IFC processing library.
11. pyRevit — open‑source scripting platform for Autodesk Revit.

*Adapt to the department's citation style; supplement with literature on design for deconstruction, steel
reuse, and the circular economy in construction.*

---

# Appendix A — Notation

`A` area · `A_v` shear area · `b` flange width · `C₁` LTB moment factor · `E` modulus of elasticity ·
`f_y` yield strength · `G` shear modulus · `g_k`/`q_k` permanent/imposed area load · `h` section depth ·
`I_y`/`I_z` second moments of area · `I_t`/`I_w` torsion/warping constants · `i_y`/`i_z` radii of gyration ·
`k` effective‑length factor · `L` length/span · `M_Ed`/`M_c,Rd`/`M_b,Rd` design/cross‑section/buckling moment ·
`N_Ed`/`N_c,Rd`/`N_b,Rd` design/cross‑section/buckling axial · `V_Ed`/`V_c,Rd` design/resistance shear ·
`W_el`/`W_pl` elastic/plastic modulus · `α`/`α_LT` imperfection factors · `β` LTB parameter ·
`γ_G`/`γ_Q` permanent/variable partial factors · `γ_M0`/`γ_M1` material partial factors ·
`δ` deflection · `ε` material factor · `λ̄`/`λ̄_LT` relative slendernesses · `φ` sway imperfection ·
`χ`/`χ_LT` flexural/LTB reduction factors · `ψ₀`/`ψ₂` combination factors. **Abbreviations:** BIM, IFC,
LTB, MILP, P‑Δ, SLS, ULS.

# Appendix B — Command‑line interface

`steelreuse --donor D.json --demand M.json --out report.html` with options: `--knockdown`; audit
`--pda audit.csv --include-unverified`; loads
`--dead --live --gamma-g --gamma-q --trib-width --col-trib-area --col-floors --trib-from-geometry`;
combination `--col-ecc --phi`; demand filter `--all-demand`; matching `--cut`; analysis
`--frame-analysis --pdelta --wind --seismic`; legacy `--beam-udl --column-axial`. Additional entry points:
`streamlit run app.py` (dashboard); `python -m steelreuse.inventory donor.json` (pre‑demolition
inventory); `python -m steelreuse.ml.train` (regenerate the ML study).

# Appendix C — Assumptions register

| Assumption | Default | Note |
|---|---|---|
| Permanent / imposed load | 3.5 / 3.0 kN/m² | set per project |
| Partial factors γ_G / γ_Q | 1.35 / 1.5 | EN 1990 (STR) |
| Beam tributary width | 3.0 m or geometry | edge = full bay (conservative) |
| Column area / floors | 9 m² / 1, or geometry / load path | use frame solve for realistic axials |
| Frame idealisation | simple braced; fixed bases | + optional sway/wind/seismic/P‑Δ |
| Column moment | 0 unless `--phi`/`--col-ecc` | member‑level notional only |
| Effective length `k` | 1.0 | conservative |
| Reclaimed knockdown | 1.0, or audit‑derived | condition × verification factor (§4.4) |
| Unverified / condition‑D donor | quarantined | `--include-unverified` to admit |
| LTB `C₁` | 1.0 | conservative |
| Compression‑flange restraint | restrained | non‑conservative if absent — warned |
| Reclaimed knockdown | 1.0 | assumes grade confirmed |
| Carbon factors | ICE v3 (1.55 / 0.10) | swappable |

*Convert this Markdown to PDF with the bundled build script; complete the title‑page placeholders before
submission.*
