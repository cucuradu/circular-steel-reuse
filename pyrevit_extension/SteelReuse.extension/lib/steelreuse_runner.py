# -*- coding: utf-8 -*-
"""Orchestration core for the SteelReuse Run Match button: turn the form's options into a CLI run.

Lives in the extension ``lib/`` (pyRevit adds it to the engine path) so any pushbutton can
``import steelreuse_runner``. IronPython-safe: stdlib only, no f-strings, no %-formatting -- the same
rules as the extractor/Apply-Matches buttons (the default IronPython 3 engine; pyRevit 6.x CPython
3.12 errors under Revit 2026).

The heavy matching engine never runs in Revit (docs/DESIGN_PRINCIPLES.md hard rule 2). Instead this module shells out
to a configured CPython interpreter via ``python -m steelreuse.cli`` -- not the pip-generated
``steelreuse.exe`` launcher, which can be blocked as an unsigned binary on locked-down Windows
(Application Control / WDAC).

Split of concerns (so most of this is testable without Revit):
  * ``build_command`` / ``output_paths`` / ``find_interpreter`` / settings -- pure, unit-tested.
  * ``run_match`` -- a synchronous subprocess call (stdlib). The Run Match button wraps this on a
    .NET background thread so Revit's UI does not freeze; that thread glue is Revit-side.
"""

import json
import os
import subprocess

# Output artifact filenames written by a run, all under one per-run folder.
_OUTPUT_NAMES = {"status": "status.json", "report": "report.html", "results": "results.json"}

_SETTINGS_FILE = "steelreuse_runner_config.json"


def output_paths(out_dir):
    """The three artifact paths for a run, all under ``out_dir``."""
    paths = {}
    for key in _OUTPUT_NAMES:
        paths[key] = os.path.join(out_dir, _OUTPUT_NAMES[key])
    return paths


def build_command(interpreter, opts, out_dir):
    """Build the argv for one match run: ``[interpreter, -m, steelreuse.cli, ...flags]``.

    ``opts`` is a plain dict (Run Match form values). Only ``donor`` and ``demand`` are required;
    everything else falls back to the CLI's own defaults when absent. The three output flags are
    always present so a run is self-contained (status.json for Apply Matches, report.html, and
    results.json for the dockable panel).
    """
    paths = output_paths(out_dir)
    # --demand takes one or several models (several -> portfolio matching). Accept a string or a list.
    demand = opts["demand"]
    demand_args = list(demand) if isinstance(demand, (list, tuple)) else [demand]
    cmd = [interpreter, "-m", "steelreuse.cli",
           "--donor", opts["donor"],
           "--demand"] + demand_args + [
           "--apply-matches-out", paths["status"],
           "--out", paths["report"],
           "--results-out", paths["results"],
           "--objective", opts.get("objective", "co2")]

    # Boolean toggles (default off; cutting-stock is the one default-on policy -> --no-cut to disable).
    if not opts.get("cut", True):
        cmd.append("--no-cut")
    for key, flag in (("frame_analysis", "--frame-analysis"), ("pdelta", "--pdelta"),
                      ("trib_from_geometry", "--trib-from-geometry"), ("all_demand", "--all-demand"),
                      ("include_unverified", "--include-unverified"), ("construction", "--construction"),
                      ("connections", "--connections"), ("moment_shape", "--moment-shape"),
                      ("pareto", "--pareto"), ("disposition", "--disposition"),
                      ("verify_match", "--verify-match")):
        if opts.get(key):
            cmd.append(flag)

    # Choice options (emit the value only when set).
    for key, flag in (("counterfactual", "--counterfactual"), ("solver", "--solver")):
        val = opts.get(key)
        if val:
            cmd.append(flag)
            cmd.append(str(val))

    # Numeric options, emitted only when truthy (else the CLI default stands).
    for key, flag in (("min_util", "--min-util"), ("phi", "--phi"),
                      ("wind", "--wind"), ("seismic", "--seismic"),
                      ("dead", "--dead"), ("live", "--live"),
                      ("gamma_g", "--gamma-g"), ("gamma_q", "--gamma-q"),
                      ("trib_width", "--trib-width"), ("col_trib_area", "--col-trib-area"),
                      ("col_floors", "--col-floors"), ("col_ecc", "--col-ecc"),
                      ("construction_live", "--construction-live"), ("wind_uplift", "--wind-uplift"),
                      ("w_overspec", "--w-overspec"), ("reserve", "--reserve"),
                      ("knockdown", "--knockdown")):
        val = opts.get(key)
        if val:
            cmd.append(flag)
            cmd.append(str(val))

    mds = opts.get("max_distinct_sections")
    if mds:
        cmd.append("--max-distinct-sections")
        cmd.append(str(mds))

    pda = opts.get("pda")
    if pda:
        cmd.append("--pda")
        cmd.append(pda)

    dcsv = opts.get("disposition_csv")
    if dcsv:
        cmd.append("--disposition-csv")
        cmd.append(dcsv)

    return cmd


def find_interpreter(candidates):
    """First path in ``candidates`` that is an existing file, else ``None``.

    The button passes the configured signed-venv python first, then any fallback guesses. A directory
    or a missing path is rejected so the caller can prompt the user to locate python.exe.
    """
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def candidate_interpreters(start_dir):
    """Auto-detect likely interpreters by walking up from ``start_dir`` looking for venvs.

    Checks each ancestor for a ``.venv*``/``venv`` folder holding ``Scripts/python.exe`` (Windows) or
    ``bin/python`` (posix). Lets the button find the signed venv next to the project without the user
    picking a file. Returns existing paths, nearest first; may be empty.
    """
    found = []
    seen = set()
    current = os.path.abspath(start_dir)
    for _ in range(6):  # a handful of levels is plenty: extension -> ... -> project parent
        try:
            names = os.listdir(current)
        except OSError:
            names = []
        for name in names:
            if not (name.startswith(".venv") or name == "venv"):
                continue
            venv = os.path.join(current, name)
            if not os.path.isdir(venv):
                continue
            for rel in (("Scripts", "python.exe"), ("bin", "python")):
                candidate = os.path.join(venv, rel[0], rel[1])
                if os.path.isfile(candidate) and candidate not in seen:
                    seen.add(candidate)
                    found.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return found


def verify_interpreter(path):
    """True iff ``path`` can actually run and import steelreuse.

    Rules out a python that merely *exists* -- e.g. a WDAC-blocked venv (process creation fails) or a
    python that does not have steelreuse installed. Used during auto-discovery so a blocked or wrong
    interpreter is never silently chosen.
    """
    if not (path and os.path.isfile(path)):
        return False
    try:
        proc = subprocess.Popen([path, "-c", "import steelreuse"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc.communicate()
        return proc.returncode == 0
    except Exception:  # process couldn't even start (blocked / not an executable)
        return False


def discover_interpreter(saved, start_dir):
    """The interpreter to use: a remembered path if it still exists, else the first auto-detected
    venv that actually runs steelreuse (see :func:`verify_interpreter`), else ``None``.

    A remembered path is trusted as-is (no per-run subprocess); discovery only verifies when it has to
    choose between candidates, so the blocked ``.venv`` is skipped in favour of the working one.
    """
    if saved and os.path.isfile(saved):
        return saved
    for candidate in candidate_interpreters(start_dir):
        if verify_interpreter(candidate):
            return candidate
    return None


def _settings_path(ext_dir):
    return os.path.join(ext_dir, _SETTINGS_FILE)


def load_settings(ext_dir):
    """Persisted run settings (interpreter path, last donor/demand, last options); {} if none."""
    path = _settings_path(ext_dir)
    if not os.path.isfile(path):
        return {}
    with open(path) as handle:
        return json.load(handle)


def save_settings(ext_dir, settings):
    """Persist run settings as JSON next to the extension."""
    with open(_settings_path(ext_dir), "w") as handle:
        json.dump(settings, handle, indent=2)


def run_match(interpreter, opts, out_dir):
    """Run one match synchronously via subprocess; return a result dict.

    ``{"ok": bool, "returncode": int, "stdout": str, "stderr": str, "paths": {...}}``. The caller
    (Run Match button) runs this on a background thread and, on success, hands ``paths["results"]``
    to the dockable panel. This function touches no Revit API, so it is safe off the UI thread.
    """
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    cmd = build_command(interpreter, opts, out_dir)
    # Revit is a GUI app, so a plain Popen would flash a console window for the child python; suppress
    # it on Windows (no-op elsewhere). Output is captured via the pipes regardless.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, creationflags=creationflags)
    out, err = proc.communicate()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": out, "stderr": err, "paths": output_paths(out_dir)}
