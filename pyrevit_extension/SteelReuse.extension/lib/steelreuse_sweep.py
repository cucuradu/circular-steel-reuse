# -*- coding: utf-8 -*-
"""Scenario-sweep orchestration: turn one fixed base config + a few varied axes into many lean runs,
then collect and rank them into a board the engineer chooses from.

End goal (see the design discussion): stop the engineer hand-running the match, tweaking one dial, and
eyeballing the diff two-at-a-time. Instead they lock the fixed problem (building, loads, donor stock),
pick 2-3 dials to vary, and the tool runs every combination at once and hands back a ranked board --
which settings reuse the most, and what each choice costs in the other currencies.

Lean-first design: every grid point is an ordinary ``steelreuse.cli`` subprocess writing its own
``results.json`` -- exactly what :func:`steelreuse_runner.run_match` already does -- so each point is
also a normal run the Compare / Results windows can open and drill into. Sweep points are *lean*: the
expensive finalist-only audit add-ons (donor-value = one MILP solve per donor, verify-match,
disposition) are stripped, because those are review tools for the chosen winners, not for exploring.
A cells-once core entrypoint can later slot in behind this same surface as a pure speed-up.

IronPython-safe (stdlib only, no f-strings): the planner / board pushbuttons run under the default
IronPython engine, the same as ``runner.py``. The planning / collecting / ranking logic here is pure,
so it is unit-tested under CPython exactly as it runs in Revit; the only impure part is
:func:`run_grid`, a bounded thread pool over an injected run function (defaults to
``steelreuse_runner.run_match``) -- each point is a separate engine *process*, so IronPython's own
threading limits never bite.
"""

import json
import os
import threading

try:                       # IronPython 2 spells it Queue; CPython 3 / IronPython 3 spell it queue.
    import queue as _queue
except ImportError:        # pragma: no cover - legacy interpreters only
    import Queue as _queue

# The per-run artifact the board reads back (matches steelreuse_runner._OUTPUT_NAMES["results"]).
RESULTS_NAME = "results.json"

# Finalist-only audit add-ons stripped from every sweep point: each is costly and only meaningful for
# a final answer (``donor_value`` re-solves the MILP once per donor; ``verify_match`` re-derives every
# feasible pair; ``disposition`` walks the unused stock). ``pareto`` is deliberately NOT here -- it is
# cheap (re-solving cached cells) and feeds the per-point trade-off.
_HEAVY_KEYS = ("donor_value", "verify_match", "disposition")

# Which direction is "best" per board metric, so :func:`rank` and :func:`pareto_front` know which way
# to sort without the caller spelling it out. More reuse / saving is better; fewer unfilled slots and
# fewer distinct sections (simpler fabrication) is better.
RANK_DIRECTION = {
    "reused": "max",
    "co2_saved_kg": "max",
    "mass_reused_kg": "max",
    "reuse_rate_pct": "max",
    "distinct_sections": "min",
    "unfilled": "min",
}

# The currencies the board's trade-off front weighs by default: most reuse / saving, fewest distinct
# section families (simpler fabrication). The board leads with this non-dominated set rather than one
# ranked metric, so the engineer sees the genuine trade-offs, not an arbitrary single winner.
DEFAULT_FRONT_METRICS = [("reused", "max"), ("co2_saved_kg", "max"),
                         ("mass_reused_kg", "max"), ("distinct_sections", "min")]


# --------------------------------------------------------------------------------------------------
# Planning (pure)
# --------------------------------------------------------------------------------------------------

def lean(opts):
    """A copy of ``opts`` with the expensive finalist-only add-ons removed (see ``_HEAVY_KEYS``)."""
    out = dict(opts)
    for key in _HEAVY_KEYS:
        out.pop(key, None)
    return out


# Axes whose value list is numeric (floats). Choice axes (objective, counterfactual, …) stay strings;
# max_distinct_sections is handled specially ('none' -> no cap). Centralised here so the planner UI
# does not duplicate per-dial parsing and the typing is unit-tested.
_FLOAT_AXES = ("min_util", "knockdown", "w_overspec", "reserve", "dead", "live", "wind", "seismic")
# Axes that are on/off toggles (e.g. --splice): tokens map to real booleans so the runner's boolean
# flag emission (``if opts.get(key)``) sees True/False, not the truthy string "off".
_BOOL_AXES = ("splice", "connections")
_TRUE_TOKENS = ("on", "true", "yes", "1")
_FALSE_TOKENS = ("off", "false", "no", "0")


def parse_values(param, text):
    """Parse a comma-separated axis value list into typed values for ``param``.

    ``max_distinct_sections`` -> ints with ``'none'`` mapping to ``None`` (no cap); numeric dials
    (see ``_FLOAT_AXES``) -> floats; boolean dials (see ``_BOOL_AXES``) -> True/False; everything
    else (choice dials like ``objective``) -> trimmed strings. Un-parseable tokens are skipped, so a
    stray comma never aborts a sweep.
    """
    tokens = [part.strip() for part in (text or "").split(",") if part.strip()]
    if param in _BOOL_AXES:
        out = []
        for tok in tokens:
            low = tok.lower()
            if low in _TRUE_TOKENS:
                out.append(True)
            elif low in _FALSE_TOKENS:
                out.append(False)
        return out
    if param == "max_distinct_sections":
        out = []
        for tok in tokens:
            if tok.lower() == "none":
                out.append(None)
            else:
                try:
                    out.append(int(float(tok)))
                except ValueError:
                    pass
        return out
    if param in _FLOAT_AXES:
        out = []
        for tok in tokens:
            try:
                out.append(float(tok))
            except ValueError:
                pass
        return out
    return tokens


def grid_size(axes):
    """Number of grid points = product of the value-list lengths (1 when nothing varies).

    ``axes`` is an ordered list of ``(param_name, [values])``. Lets the planner show a live count and
    refuse a grid that is too large before any run starts.
    """
    n = 1
    for _, values in axes:
        n *= max(len(values), 1)
    return n


def expand_grid(fixed, axes):
    """Cartesian product of the varied ``axes`` over the ``fixed`` base opts.

    ``fixed`` is the locked base options dict (the problem + structural invariants). ``axes`` is an
    ordered list of ``(param_name, [values])`` the sweep varies. Returns one opts dict per grid point:
    a copy of ``fixed`` with that point's value set for each varied param. Deterministic order (the
    first axis varies slowest), so point ids / output folders are stable across re-runs.
    """
    points = [dict(fixed)]
    for param, values in axes:
        expanded = []
        for base in points:
            for value in values:
                row = dict(base)
                row[param] = value
                expanded.append(row)
        points = expanded
    return points


def _slug(value):
    """Filesystem- and label-safe token for an axis value (bool / None / number / string)."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    if value is None:
        return "none"
    text = str(value).replace(".", "p").replace(" ", "_")
    return text.replace("/", "_").replace("\\", "_")


def point_label(axes, opts):
    """Human label for a grid point: only the swept axes and their values (the base is the same for
    every point), e.g. ``objective=members, min_util=0.6``; ``single run`` when nothing varies."""
    parts = ["%s=%s" % (param, opts.get(param)) for param, _ in axes]
    return ", ".join(parts) if parts else "single run"


def point_id(axes, opts):
    """Stable, filesystem-safe id for a grid point's output folder, from its swept values only."""
    parts = [param + "-" + _slug(opts.get(param)) for param, _ in axes]
    return "__".join(parts) if parts else "run"


def plan(fixed, axes, out_root):
    """Full sweep plan: one descriptor per grid point -> ``{id, label, opts, out_dir}``.

    ``opts`` are lean-ified here (the executor just runs them); ``out_dir`` is ``out_root/<id>`` so each
    point's ``results.json`` lands in its own folder and stays a normal, openable run.
    """
    rows = []
    for opts in expand_grid(fixed, axes):
        pid = point_id(axes, opts)
        rows.append({
            "id": pid,
            "label": point_label(axes, opts),
            "opts": lean(opts),
            "out_dir": os.path.join(out_root, pid),
        })
    return rows


# --------------------------------------------------------------------------------------------------
# Execution (impure: a bounded thread pool over an injected run function)
# --------------------------------------------------------------------------------------------------

def cpu_total():
    """Logical processor count (Windows ``NUMBER_OF_PROCESSORS``, else ``multiprocessing``); >= 1."""
    n = 0
    try:
        n = int(os.environ.get("NUMBER_OF_PROCESSORS", "0"))
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        try:
            import multiprocessing
            n = multiprocessing.cpu_count()
        except Exception:  # noqa: BLE001 - cpu_count can be unavailable; fall back to a safe default
            n = 2
    return max(1, n)


def default_workers():
    """How many engine processes to run at once by default: one fewer than the CPU count, so Revit
    keeps a core (never below 1). Each point is a separate process, so this caps real parallelism."""
    return max(1, cpu_total() - 1)


def clamp_workers(requested):
    """The effective worker count for a requested value: at least 1, never more than the logical CPU
    count.

    More workers than cores gives no speed-up -- the work is CPU-bound, so throughput plateaus at the
    core count -- and only risks exhausting RAM on smaller machines (each worker is a separate engine
    process holding the models). Un-parseable input falls back to :func:`default_workers`.
    """
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return default_workers()
    return max(1, min(n, cpu_total()))


def run_grid(plan_rows, interpreter, run_fn=None, max_workers=None, on_done=None):
    """Run every planned point, at most ``max_workers`` at a time; return results in plan order.

    ``run_fn(interpreter, opts, out_dir)`` defaults to :func:`steelreuse_runner.run_match`; injecting it
    keeps this unit-testable without spawning real engines. ``max_workers`` defaults to
    :func:`default_workers` and is clamped to the point count. ``on_done(done, total, row, result)`` is
    an optional progress callback (for the WPF progress bar), called once per finished point under a
    lock so the count is consistent.
    """
    if run_fn is None:
        import steelreuse_runner
        run_fn = steelreuse_runner.run_match
    total = len(plan_rows)
    if total == 0:
        return []
    if max_workers is None:
        max_workers = default_workers()
    max_workers = max(1, min(max_workers, total))

    results = [None] * total
    pending = _queue.Queue()
    for i, row in enumerate(plan_rows):
        pending.put((i, row))
    lock = threading.Lock()
    done = {"n": 0}

    def worker():
        while True:
            try:
                i, row = pending.get_nowait()
            except _queue.Empty:
                return
            result = run_fn(interpreter, row["opts"], row["out_dir"])
            results[i] = result
            if on_done is not None:
                with lock:
                    done["n"] += 1
                    on_done(done["n"], total, row, result)

    threads = [threading.Thread(target=worker) for _ in range(max_workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# --------------------------------------------------------------------------------------------------
# Collecting + ranking (pure)
# --------------------------------------------------------------------------------------------------

def collect_point(row):
    """Read one finished point's ``results.json`` into a compact board record.

    ``row`` is a :func:`plan` descriptor. A missing / unreadable / failed run yields a record with
    ``ok=False`` and ``None`` metrics, so the board greys it out instead of dropping it (the engineer
    still sees that the combination was tried). ``unfilled`` is ``slots - reused`` when both are
    present, else the length of the unfilled list.
    """
    rec = {"id": row.get("id"), "label": row.get("label"), "out_dir": row.get("out_dir", ""),
           "ok": False, "on_front": False,
           "reused": None, "co2_saved_kg": None, "mass_reused_kg": None,
           "distinct_sections": None, "unfilled": None, "reuse_rate_pct": None,
           "proven_optimal": None, "solver_status": "", "objective": ""}
    path = os.path.join(row.get("out_dir", ""), RESULTS_NAME)
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return rec
    kpis = data.get("kpis", {})
    rec["ok"] = True
    for key in ("reused", "co2_saved_kg", "mass_reused_kg", "distinct_sections",
                "reuse_rate_pct", "proven_optimal"):
        rec[key] = kpis.get(key)
    rec["solver_status"] = kpis.get("solver_status", "")
    rec["objective"] = kpis.get("objective", "")
    slots, reused = kpis.get("slots"), kpis.get("reused")
    rec["unfilled"] = (slots - reused) if (slots is not None and reused is not None) \
        else len(data.get("unfilled", []))
    return rec


def collect(plan_rows):
    """Board records for every planned point, in plan order (see :func:`collect_point`)."""
    return [collect_point(row) for row in plan_rows]


def rank(records, metric):
    """Records ordered best-first by ``metric`` (direction from ``RANK_DIRECTION``).

    Records missing the metric (failed runs) sort last regardless of direction, so a broken point never
    masquerades as the winner.
    """
    direction = RANK_DIRECTION.get(metric, "max")

    def key(rec):
        value = rec.get(metric)
        if value is None:
            return (1, 0.0)               # missing -> always last
        return (0, -value if direction == "max" else value)

    return sorted(records, key=key)


def _dominates(a, b, metrics):
    """True iff record ``a`` is at-least-as-good as ``b`` on every metric and strictly better on one."""
    ge_all, gt_any = True, False
    for key, direction in metrics:
        av, bv = a.get(key), b.get(key)
        if av is None or bv is None:
            return False
        if direction == "max":
            better, equal_or_better = av > bv, av >= bv
        else:
            better, equal_or_better = av < bv, av <= bv
        if not equal_or_better:
            ge_all = False
            break
        if better:
            gt_any = True
    return ge_all and gt_any


def pareto_front(records, metrics):
    """The non-dominated records over ``metrics`` (list of ``(key, 'max'|'min')``), in input order.

    A point is dominated when another is at-least-as-good on every chosen metric and strictly better on
    at least one -- so the front is the set of genuine trade-offs (you can't improve one currency
    without giving up another). Failed runs (any chosen metric ``None``) are never on the front.
    """
    live = [r for r in records if all(r.get(key) is not None for key, _ in metrics)]
    return [r for r in live if not any(_dominates(other, r, metrics) for other in live if other is not r)]


def mark_front(records, metrics=None):
    """Set ``on_front`` on each record (True iff it is on the non-dominated front); return ``records``.

    Mutates in place so the board can bind the flag straight to a row highlight. ``metrics`` defaults to
    :data:`DEFAULT_FRONT_METRICS`.
    """
    front = pareto_front(records, metrics or DEFAULT_FRONT_METRICS)
    front_ids = set(id(r) for r in front)
    for rec in records:
        rec["on_front"] = id(rec) in front_ids
    return records
