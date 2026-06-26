# National Annex q_k sources — provenance register

The imposed-load values `q_k` (kN/m²) are **Nationally Determined Parameters**: each country's National
Annex (NA) to EN 1991-1-1 sets its own. This file records **where each value in
`src/steelreuse/core/loads.py` (`NATIONAL_ANNEXES`) came from** so a reviewer can trace and re-verify
it. The values were parsed from the cited documents on **2026-06-20/21** (an earlier session — not
re-derived here).

> **Why no PDFs are bundled.** EN standards and most National Annexes are **copyright-protected**
> (CEN/national standards bodies); we cannot redistribute the PDFs inside the repo. The free official
> ones below can be downloaded from the issuing body; the paywalled ones must be bought from the
> national standards body. **For certified use, confirm every `q_k` against the governing NA.**

## Base standard

| Code | Document | Status | Notes |
|---|---|---|---|
| `en` | EN 1991-1-1:2002, Table 6.2 (floors A–E) | base recommended values | tool default; English text from the JRC/published PDF. `office-B` kept at the EN upper bound 3.0. |

## Read from official, free national documents

| Code | National Annex document | Categories entered |
|---|---|---|
| `dk` | **Denmark** — DS/EN 1991-1-1 DK NA:2013 (Tables 6.2/6.8/6.10) | residential-A 1.5, stairs-A 3.0, office-B 2.5, congress-C1 2.5, traffic-F 2.5, roof-H 0.0 |
| `fi` | **Finland** — SFS-EN 1991-1-1 NA, Ministry of the Environment Decree 4/16 | office-B 2.5, congress-C1 2.5, C2 3.0, C3 4.0, C5 6.0 |
| `cy` | **Cyprus** — CYS EN 1991-1-1 NA (Tables 6.2/6.8) | stairs-A 3.0, balcony-A 4.0, retail-D1 5.0, traffic-F 2.5 |
| `es` | **Spain** — CTE DB-SE-AE, Tabla 3.1 (the Spanish code, not a CEN NA) | office-B 2.0, retail-D1 5.0 |
| `be` | **Belgium** — NBN EN 1991-1-1 ANB (via the Buildwise fiche) | stairs-A 3.0, balcony-A 4.0, retail-D1 5.0, traffic-F 2.5 |

## Partial — a few values from secondary/known sources (verify before use)

| Code | Document | Categories entered |
|---|---|---|
| `it` | **Italy** — NTC 2018, Tab. 3.1.II | storage-E1 6.0, roof-H 0.5 |
| `uk` | **United Kingdom** — BS EN 1991-1-1 NA | residential-A 1.5, office-B 2.5 |

## Paywalled — inherit the EN base until verified values are entered

`de` (DIN EN 1991-1-1/NA), `fr` (NF EN 1991-1-1/NA), `nl` (NEN-EN 1991-1-1/NB), `ie` (I.S. EN
1991-1-1/NA). Only paid copies were found, so these carry **no overrides** — they use the EN
recommended values. Add a value as a single dict entry in `NATIONAL_ANNEXES` once verified.

---

*To turn this register into a folder of the actual free PDFs (DK/FI/CY/ES/BE), the official download
links need locating + licence-checking first — ask and I will gather and verify them.*
