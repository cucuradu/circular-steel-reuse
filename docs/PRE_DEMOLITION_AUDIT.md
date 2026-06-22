# Pre-demolition audit (PDA)

A **pre-demolition audit** is the survey carried out on a building before it is demolished or deeply
refurbished, to inventory its materials and record — *per member* — the **quantity**, the **physical
condition**, and the **basis on which the material grade can be trusted**. It is the upstream process
whose deliverable is the donor model this tool consumes: the matcher does not *replace* a PDA, it
*depends* on one. This document describes how the audit is represented, why it matters, and how to
supply it.

## Why it belongs in the workflow

Without audit data the tool would have to assume every reclaimed member is as-new and of a known grade
— an assumption that is both non-conservative and unverifiable, and one that quietly inflates the
embodied-carbon savings claimed for steel that may not actually be reusable. The PDA layer closes that
gap with two levers the deterministic EN 1993 check already understands:

1. a per-member **knockdown** on the yield strength `f_y` (condition / verification uncertainty), and
2. a **quarantine** decision — unverified or unsuitable stock is excluded from the certified supply,
   exactly the way an unmapped or fuzzy-matched section is, so it can never silently enter analysis.

This is consistent with reuse-specific guidance (e.g. SCI **P427**, *Structural Steel Reuse*), which
permits reliance on reclaimed steel only where its grade is established (mill certificate or coupon
test) and its condition is sound. It also aligns the tool with the regulatory direction of travel:
the EU **Construction & Demolition Waste Management Protocol**, France's mandatory *Diagnostic PEMD*,
the EU **Level(s)** framework, and Italy's **CAM Edilizia** reused-content criteria all push the
pre-demolition audit upstream of any reuse claim.

## Honest by default

The tool never *invents* a condition. A donor member that carries **no audit data at all** is treated
as legacy input — admitted to supply at the run's default knockdown (`--knockdown`, default `1.0`), so
existing results are unchanged. Quarantine and derived knockdowns engage **only** when the audit
actually recorded something. Absence of data means "not audited", not "fine" (DESIGN_PRINCIPLES.md rule 5).

## The two audit facts → numbers

### Verification basis (how far the declared grade is trusted)

| `verification_status` | f_y factor | meaning |
|---|---|---|
| `mill_cert`     | 1.00 | original mill certificate / full traceability |
| `coupon_tested` | 1.00 | sampled and tensile-tested → grade established by test |
| `documented`    | 0.95 | design drawings/records state the grade, no cert or test |
| `visual_only`   | 0.90 | grade only assumed from era/appearance |
| `unverified` *(or blank)* | — | **quarantined** unless `--include-unverified` |

### Physical condition (an A–D survey scale)

| `condition_grade` | f_y factor | meaning |
|---|---|---|
| `A` | 1.00 | as-new: negligible corrosion, no deformation/damage |
| `B` | 0.95 | minor: light surface corrosion, cosmetic only |
| `C` | 0.85 | significant: measurable section loss / minor deformation |
| `D` | — | **quarantined**: unsuitable (heavy corrosion, distortion, damage) |

The applied knockdown is the **product** of the two factors (e.g. condition `B` + `documented`
= 0.95 × 0.95 = 0.9025), unless the auditor sets an explicit `knockdown` on the member, which
overrides the derivation. A derived or explicit knockdown below the floor (`MIN_KNOCKDOWN = 0.30`)
quarantines the member rather than silently zeroing its capacity.

`recoverable_length_mm` records the usable length after de-construction (after cutting connections);
it defaults to the member's physical length when not surveyed and feeds the matcher's length/cutting
constraints.

## How to supply audit data

Two interchangeable routes — the fields live on each donor member either way:

1. **In the model JSON.** The extractor (or a later edit) sets the PDA fields directly on each
   `ExtractedMember`: `condition_grade`, `verification_status`, `knockdown`, `defects`,
   `recoverable_length_mm`.
2. **As a separate CSV** merged at run time with `--pda audit.csv`. This lets the audit live alongside
   the BIM export rather than inside it. Columns (only `id` is required; blanks are ignored):

   ```csv
   id,condition_grade,verification_status,knockdown,recoverable_length_mm,defects
   D1,A,mill_cert,,6000,
   D2,B,documented,,,light surface corrosion
   D3,A,coupon_tested,,,
   D4,C,visual_only,,,minor web dent
   D5,A,unverified,,,no paperwork
   D6,D,visual_only,,,severe corrosion
   ```

   A blank template ships at [`docs/pda_template.csv`](pda_template.csv).

3. **In Revit, via the Set Audit button.** Select the framing/columns you surveyed, then
   **SteelReuse tab → Review panel → Set Audit**, and enter the condition grade, verification basis,
   knockdown, recoverable length and defects. The values are written to schedulable **SteelReuse**
   shared parameters (`Reuse Condition Grade`, `Reuse Verification`, `Reuse Knockdown`,
   `Reuse Recoverable Length (mm)`, `Reuse Defects`) on each element, alongside the reuse passport.
   The next **Extract Steel** reads them straight back onto each member, so the audit flows into the
   match exactly like route 1 — no separate CSV needed. The same **Review** panel also has
   **Review Extraction** / **PDA Report** (read the data-quality and audit-coverage reports without
   running a match) and **Highlight Problems** / **Clear Highlight** (colour the model by review
   severity). See [`pyrevit_extension/README.md`](../pyrevit_extension/README.md#use-the-review-panel).

## CLI

```bash
# merge an audit CSV; unverified/unsuitable members are quarantined
steelreuse --donor donor.json --demand demand.json --pda audit.csv --out report.html

# knowingly admit unverified stock at a conservative knockdown instead of quarantining it
steelreuse --donor donor.json --demand demand.json --pda audit.csv --include-unverified

# a run with no audit data: the default knockdown applies to all donor members (legacy behaviour)
steelreuse --donor donor.json --demand demand.json --knockdown 0.9
```

The console prints an audit line (members audited / admitted / quarantined / average knockdown) and
the HTML report gains a **Pre-demolition audit** section plus a per-assignment **Provenance** column.
The material passport (`core/carbon.py`) records each member's verification basis and condition grade.

## Scope

The PDA layer models *how audit findings flow into the structural and carbon results*. It does not
perform the survey, design coupon-test programmes, or assess weldability of old steel or connection
condition — those remain the engineer's responsibility (see `docs/METHODOLOGY.md` §12, out of scope).
The value here is making the audit an explicit, auditable input rather than a silent assumption.
