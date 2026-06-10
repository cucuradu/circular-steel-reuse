---
title: Routine - verify test suite (all tests pass?)
use_pytest: true
num_ctx: 16384
---

Summarize the pytest run above. Be terse.

If ALL tests passed: write one line — "All N tests passed." and stop. Do not add advice or suggestions.

If there were failures:
- List every failing test by name with its error message verbatim (do not paraphrase).
- Group failures by suspected module (loads, sections, carbon, match, etc.).
- Note any skipped tests and the skip reason if shown.

If there were warnings: list them verbatim. Do not add advice.

Do NOT suggest fixes. Do NOT write code. Do NOT pad with generic recommendations.
