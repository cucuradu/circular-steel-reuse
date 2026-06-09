# Case study — real Revit building models (US / AISC)

This applies the full pipeline to **real models extracted from Revit** with the pyRevit extractor —
not synthetic data. It shows what the tool does on a messy, real-world input and documents the
limitations it surfaces honestly.

The models (`pyrevit_extension/donor_test2.json`, `demand_test2.json`) are US/AISC steel frames:

| Model | Members | Columns | Beams |
|-------|---------|---------|-------|
| Donor (supply, building to deconstruct) | 1016 | 74 | 942 |
| Demand (new design) | 270 | 54 | 216 |

Reproduce:

```powershell
steelreuse --donor pyrevit_extension/donor_test2.json --demand pyrevit_extension/demand_test2.json --out reports/case_study.html
```

## Result (default area-load model, steel-only demand)

```
Mapping: 434 mapped, 0 fuzzy, 582 unknown of 1016 members
Supply 434 | demand slots 349 | reused 140
CO2e saved by matches: 16,496 kg  (full donor stock potential: 315,275 kg)
Narrative source: deterministic (rejected gemini output)
```

- **Section mapping:** 434 of the 1016 donor members map to catalog **W-shapes**; **582 are "unknown"**
  and are *reported, never guessed* — they are concrete members, bar joists, channels (C/MC), HSS and
  L-angles, all intentionally out of the current W-shapes-only scope (mono-symmetric/hollow shapes
  need shape-aware checks; see [FUTURE_IMPROVEMENTS.md](../FUTURE_IMPROVEMENTS.md)). 0 fuzzy matches —
  nothing entered the analysis on a guessed identity.
- **Matching:** the new design has **349 steel slots** (after steel-only filtering and multi-span
  splitting); **140 are filled by reclaimed members (40%)** that pass every EN 1993-1-1 load
  combination. The rest are listed as needing new steel.
- **Carbon:** the matched reuse saves **≈ 16.5 t CO₂e** on the honest *avoided-new* basis (the new
  section each slot would otherwise have required), cleanly separated from the **≈ 315 t** total
  embodied carbon held in the whole donor stock (the theoretical ceiling if everything were reused).
- **AI guard fired for real:** the LLM narrative was generated and then **rejected by the
  anti-hallucination check** (it introduced a number not in the computed set), so the report fell back
  to the deterministic summary — exactly the safety behaviour the design requires.

## Limitations this run surfaced (honest reporting)

1. **Column coordinates missing → frame analysis falls back.** These models were extracted with an
   earlier extractor that recorded only location-*curve* endpoints, so the point-placed **columns
   carry no x,y**. Running `--frame-analysis` therefore detects unstable (disconnected) nodes and
   **gracefully falls back to the per-member analytic load path** (no crash; identical headline
   numbers). The fix is a **re-extraction** with the current extractor (which captures
   `LocationPoint` column coordinates) — a human/Revit task. Until then the connected load path
   (multi-storey column accumulation) cannot engage on these specific files.
2. **W-shapes only.** ~57% of donor members fall in the `unknown` bucket because they are not
   doubly-symmetric I-sections. Extending the catalog + checks to C/HSS/L is the obvious next step to
   raise the mappable fraction.
3. **Member-level pre-feasibility.** As everywhere in this tool: no connection design, no physical
   material verification — decision support, not a code-certified result.

## Takeaway

On a real 1000-member building the tool runs end-to-end in seconds, maps the in-scope steel without
guessing, finds a structurally-valid 40% reuse rate, books a defensible 16.5 t CO₂e saving, and is
candid about what it cannot yet do (non-W shapes, and the load path until the model is re-extracted
with column coordinates).
