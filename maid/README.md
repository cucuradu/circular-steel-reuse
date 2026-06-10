# The Maid 🧹

A **local, read-only batch worker** powered by Ollama + `qwen2.5-coder:32b`. AI agents (Claude/Opus)
queue small text-only chores; the Maid does them on your machine and writes reports. It never edits
code, never touches git, and never blocks the agents — *it just cleans up.*

> **Contract:** the Maid only **drafts, summarizes, and triages**. A reviewing agent turns anything
> useful into real code or numbers. The Maid never produces engineering numbers (project hard rule #1:
> *the LLM never does arithmetic*). See [`MAID_SYSTEM_PROMPT.md`](MAID_SYSTEM_PROMPT.md).

## Layout
```
maid\
  Run-Maid.ps1            you start this; it watches the queue
  MAID_SYSTEM_PROMPT.md   guardrails prepended to every task
  queue\                  agents drop one NNNN-slug.md task file here
  reports\                the Maid writes NNNN-slug-<timestamp>.md here
  done\                   processed task files move here (audit trail)
  logs\maid.log           one line per task (START / OK / ERROR / WARN)
```

## Run it
Prereq: the Ollama app is running (it serves `http://localhost:11434`).

```powershell
# from anywhere — paths are resolved from the script location
.\maid\Run-Maid.ps1            # WATCH: loops forever, picks up new tasks; Ctrl-C to stop
.\maid\Run-Maid.ps1 -Once      # DRAIN: process everything in the queue once, then exit
```
Useful switches: `-IntervalSeconds 30`, `-TimeoutSec 900`, `-DefaultNumCtx 16384`, `-Model <name>`.
`qwen2.5-coder:32b` is slow — a watch loop running in the background (or `-Once` under Task Scheduler
overnight) is the comfortable way to use it.

## Task file format (what an agent writes into `queue\`)
```markdown
---
title: Verify the test suite and summarize failures
use_pytest: true            # optional — runs `python -m pytest --tb=short -q`, feeds output as context
files:                      # optional — source files fed to the Maid read-only
  - tests/test_loads.py
  - src/steelreuse/core/loads.py
num_ctx: 16384              # optional — context window (default 16384)
---
Plain-language instruction for the Maid goes here.
```
`files:` paths are **relative to the repo root** (`circular-steel-reuse\`). Front-matter is optional;
a task can be just a body. Tasks are processed oldest-first.

## Workflow (for agents and for you)
1. **Agent queues work.** When a Claude session spots a delegatable, *read-only, text-output* chore
   (summarize a doc, find inconsistencies, brainstorm test gaps, triage a pytest run), it writes a new
   `NNNN-slug.md` into `queue\` — it does **not** wait for the result.
2. **Maid works.** You keep `Run-Maid.ps1` running (or scheduled). Each task → one report in `reports\`;
   the task file moves to `done\`.
3. **Agent consumes.** A later Claude session reads `reports\`, acts on anything useful (writes the real
   tests, fixes the stale numbers, etc.), then sets the consumed report aside.

## Good Maid jobs ✅ / Not Maid jobs ❌
- ✅ Summaries, doc-consistency checks, test-gap checklists, changelog/commit drafts, readability notes.
- ❌ Anything requiring arithmetic, deciding Eurocode correctness, editing files, or running the app.
  Those stay with the deterministic core and the reviewing agent.
