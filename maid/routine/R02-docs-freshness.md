---
title: Routine - docs freshness check
files:
  - README.md
  - CHANGELOG.md
  - TODO.md
  - src/steelreuse/core/loads.py
  - src/steelreuse/core/sections.py
  - src/steelreuse/core/carbon.py
  - src/steelreuse/match/optimize.py
num_ctx: 32768
---

Read README.md, CHANGELOG.md, and TODO.md. Then read the four source files.

For each section of the README that describes functionality, flag whether the description still matches the source code (function names, module structure, workflow).

Produce a checklist — one row per README claim:
- [OK] claim is confirmed by the provided source files
- [STALE?] claim conflicts with what the provided source files show — quote both
- [UNVERIFIABLE] claim references a file or symbol not in the provided context — say so explicitly, do NOT call it absent or non-existent

If a section has no issues, write "Nothing to flag." Do not add generic advice.

Do NOT compute. Do NOT suggest rewrites. Just surface staleness for a reviewing agent.
