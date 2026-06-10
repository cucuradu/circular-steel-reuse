---
title: Routine - TODO triage
files:
  - TODO.md
  - CHANGELOG.md
  - FUTURE_IMPROVEMENTS.md
num_ctx: 16384
---

Read TODO.md, CHANGELOG.md, and FUTURE_IMPROVEMENTS.md.

Read the ENTIRE TODO.md from top to bottom — including any completed/archived/done sections marked with [x] or similar. Do not skip any section.

Produce a triage table for EVERY item in TODO.md — open and completed:

| Item (verbatim, one row per checkbox) | Category | Status | Already in CHANGELOG? | Duplicate of? |
|---------------------------------------|----------|--------|-----------------------|---------------|

- Category: validation, docs, testing, refactor, feature, thesis, blocked
- Status: open ([ ]) or done ([x])
- "Already in CHANGELOG?" = yes + quote the CHANGELOG line if you find a matching entry; no otherwise
- "Duplicate of?" = row number of the earlier item if this is a duplicate; blank if not

At the end, list only the open items you judge most likely to be blocking thesis submission, with a one-sentence reason drawn from the text — not generic advice. If you cannot judge priority from the text, say so explicitly rather than listing all items.

Do NOT suggest solutions. Do NOT compute. Quote everything verbatim.
