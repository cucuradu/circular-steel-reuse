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
  * ``build_command`` / ``output_paths`` / ``discover_interpreter`` / settings -- pure, unit-tested.
  * ``run_match`` -- a synchronous subprocess call (stdlib). The Run Match button wraps this on a
    .NET background thread so Revit's UI does not freeze; that thread glue is Revit-side.
"""

import json
import os
import subprocess
import webbrowser

# Output artifact filenames written by a run, all under one per-run folder. ``evidence`` is the
# signable per-run evidence package (Roadmap §1.1) and ``mismatch`` the donor-row mismatch log
# (Roadmap §1.2); both are always written so a run is self-contained and reviewable.
_OUTPUT_NAMES = {"status": "status.json", "report": "report.html", "results": "results.json",
                 "evidence": "evidence.json", "mismatch": "mismatch.csv"}

_SETTINGS_FILE = "steelreuse_runner_config.json"
# Transient view state (the element ids Highlight Problems coloured) lives in its OWN file, not the
# settings config: that list runs to ~1000 ids and every button reads the settings on each click.
_HIGHLIGHT_FILE = "steelreuse_highlight.json"


def reports_dir(ext_root):
    """The single fixed output folder for ALL SteelReuse artifacts: ``<repo>/steelreuse_reports``.

    Anchored to the repository root -- two levels above the extension dir, which is
    ``<repo>/pyrevit_extension/SteelReuse.extension`` -- so every button (Run Match, Value Case,
    Review) and the extractor write to the SAME project-root folder, instead of scattering outputs
    next to each model or inside the extension's own code folder. The extractor derives the identical
    path from its own location (``<repo>/extractor/pyrevit_extract.py``). Pure: it only computes the
    path (callers ``makedirs`` as needed); the folder is gitignored via ``steelreuse_reports/``.
    """
    repo_root = os.path.dirname(os.path.dirname(ext_root))
    return os.path.join(repo_root, "steelreuse_reports")


def output_paths(out_dir):
    """The three artifact paths for a run, all under ``out_dir``."""
    paths = {}
    for key in _OUTPUT_NAMES:
        paths[key] = os.path.join(out_dir, _OUTPUT_NAMES[key])
    return paths


# Review-mode artifact filenames (a review run, no match).
_REVIEW_NAMES = {"review_json": "review.json", "problems": "problems.html",
                 "pda_html": "pda.html", "pda_csv": "audit.csv"}


def review_paths(out_dir):
    """The four review artifact paths under ``out_dir``."""
    paths = {}
    for key in _REVIEW_NAMES:
        paths[key] = os.path.join(out_dir, _REVIEW_NAMES[key])
    return paths


def build_review_command(interpreter, opts, out_dir):
    """argv for a review run: ``python -m steelreuse.validate_extraction donor ...artifacts``.

    Review needs only the donor model -- no demand, no match. ``opts`` carries ``donor`` (required)
    and optional ``pda``. All four artifacts are always written so the buttons are self-contained.
    """
    paths = review_paths(out_dir)
    cmd = [interpreter, "-m", "steelreuse.validate_extraction", opts["donor"],
           "--review-json", paths["review_json"],
           "--report", paths["problems"],
           "--pda-report", paths["pda_html"],
           "--pda-out", paths["pda_csv"]]
    pda = opts.get("pda")
    if pda:
        cmd.append("--pda")
        cmd.append(pda)
    return cmd


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
           # No per-run HTML report: the Run Match / Results windows render it on demand from
           # results.json (steelreuse_results_view), so a file per run is just clutter.
           "--no-report",
           "--results-out", paths["results"],
           # Always emit the signable evidence package + donor mismatch log, so every run is
           # self-contained and reviewable (the Results window surfaces both).
           "--evidence-out", paths["evidence"],
           "--mismatch-csv", paths["mismatch"],
           "--objective", opts.get("objective", "co2")]

    # Boolean toggles (default off; cutting-stock is the one default-on policy -> --no-cut to disable).
    if not opts.get("cut", True):
        cmd.append("--no-cut")
    for key, flag in (("frame_analysis", "--frame-analysis"), ("pdelta", "--pdelta"),
                      ("trib_from_geometry", "--trib-from-geometry"), ("all_demand", "--all-demand"),
                      ("include_unverified", "--include-unverified"), ("construction", "--construction"),
                      ("connections", "--connections"), ("moment_shape", "--moment-shape"),
                      ("pareto", "--pareto"), ("disposition", "--disposition"),
                      ("donor_value", "--donor-value"),
                      ("verify_match", "--verify-match")):
        if opts.get(key):
            cmd.append(flag)

    # Choice options (emit the value only when set).
    for key, flag in (("counterfactual", "--counterfactual"), ("solver", "--solver"),
                      ("carbon_dataset", "--carbon-dataset"),
                      ("occupancy", "--occupancy"), ("roof_occupancy", "--roof-occupancy")):
        val = opts.get(key)
        if val:
            cmd.append(flag)
            cmd.append(str(val))

    # Imposed-load reduction is ON by default; emit the disable flag only when turned off.
    if opts.get("load_reduction") is False:
        cmd.append("--no-load-reduction")

    # National Annex defaults to EN base; emit only when a different NA is chosen.
    na = opts.get("national_annex")
    if na and na != "en":
        cmd.append("--national-annex")
        cmd.append(str(na))

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


def run_inventory_template(interpreter, target):
    """Write a blank donor-inventory template (xlsx/csv) by shelling out to the engine CLI.

    ``target``'s extension picks the format (.xlsx else .csv). Returns the same dict shape as
    run_match. Synchronous and Revit-free, so it is safe to call from a button handler directly
    (the write is near-instant -- no background thread needed).
    """
    cmd = [interpreter, "-m", "steelreuse.cli", "--inventory-template", target]
    out_dir = os.path.dirname(target) or "."
    return _run_logged(cmd, out_dir, {"template": target}, "inventory_template.log")


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


def _highlight_path(ext_dir):
    return os.path.join(ext_dir, _HIGHLIGHT_FILE)


def load_highlight(ext_dir):
    """The element ids currently highlighted by Highlight Problems (list of str); [] if none.

    Kept out of the settings config so the bulky id list is not parsed by every other button.
    """
    path = _highlight_path(ext_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as handle:
            return json.load(handle).get("highlighted_ids", [])
    except Exception:  # noqa: BLE001 -- a corrupt/stale file just means nothing to clear
        return []


def save_highlight(ext_dir, ids):
    """Persist the highlighted element ids (list of str) for Clear Highlights to undo later."""
    with open(_highlight_path(ext_dir), "w") as handle:
        json.dump({"highlighted_ids": list(ids)}, handle, indent=2)


# Windows NTSTATUS-style exit codes that mean "the OS killed the process", not a clean Python error
# (a Python exception exits 1 with a traceback). These appear as large-magnitude negative returncodes.
_CRASH_CODES = {
    -1073741510: "terminated (Ctrl-C / console-close, 0xC000013A)",
    -1073741819: "access violation / native crash (0xC0000005)",
    -1073741795: "illegal instruction (0xC000001D)",
    -1073740791: "stack buffer overrun (0xC0000409)",
    -1073741515: "a required DLL was not found (0xC0000135)",
}


def describe_returncode(rc):
    """A human hint for an abnormal subprocess exit code, or '' for a normal (0..255) exit.

    Large negative codes are Windows process-kills: the engine was terminated by the OS (antivirus /
    Application Control / a blocked native binary such as the bundled CBC solver), not a Python error.
    Such a kill usually leaves no log, so the button should explain the number instead of just showing
    it. The same command run from a terminal succeeding confirms it is the in-Revit launch context.
    """
    if 0 <= rc <= 255:
        return ""
    what = _CRASH_CODES.get(rc, "abnormal termination (0x%08X)" % (rc & 0xFFFFFFFF))
    return ("The engine process was killed by Windows: " + what + ". This is not a Python error and "
            "usually leaves no log. Most likely antivirus or Application Control killed python.exe or "
            "the bundled CBC solver. If the same 'python -m steelreuse.cli ...' runs fine in a "
            "terminal, the problem is the in-Revit launch context (an AV/policy exclusion is needed).")


def _run_logged(cmd, out_dir, paths, log_name):
    """Shell the engine out, streaming stdout+stderr to ``out_dir/log_name`` so the output survives
    even a hard OS kill (a pipe + communicate() would return empty when the child is terminated).

    Runs the interpreter unbuffered (``-u``) so the log flushes in real time, suppresses the console
    window (Revit is a GUI app), and puts the child in its own process group so it does not inherit
    Revit's console-control events (the 0xC000013A kill). Returns the run_match/run_review result dict.
    """
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    cmd = [cmd[0], "-u"] + cmd[1:]
    log_path = os.path.join(out_dir, log_name)
    creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    with open(log_path, "w") as logfh:
        proc = subprocess.Popen(cmd, stdout=logfh, stderr=subprocess.STDOUT,
                                creationflags=creationflags)
        proc.wait()
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            combined = fh.read()
    except Exception:  # noqa: BLE001 -- a missing log must never mask the returncode
        combined = ""
    paths = dict(paths)
    paths["log"] = log_path
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": combined, "stderr": "", "paths": paths}


def run_match(interpreter, opts, out_dir):
    """Run one match synchronously via subprocess; return a result dict.

    ``{"ok": bool, "returncode": int, "stdout": str, "stderr": str, "paths": {...}}`` (``stdout`` is
    the combined child log; ``paths['log']`` is its file). The caller (Run Match button) runs this on a
    background thread and, on success, hands ``paths['results']`` to the dockable panel. This function
    touches no Revit API, so it is safe off the UI thread.
    """
    return _run_logged(build_command(interpreter, opts, out_dir), out_dir,
                       output_paths(out_dir), "run.log")


def run_review(interpreter, opts, out_dir):
    """Run a review synchronously via subprocess; return the same dict shape as run_match."""
    return _run_logged(build_review_command(interpreter, opts, out_dir), out_dir,
                       review_paths(out_dir), "review.log")


# Value-case artifact filenames.
_VALUE_CASE_NAMES = {
    "writeback": "value_case.json",
    "results": "value_case_results.json",
    "csv": "value_case.csv",
}


def value_case_paths(out_dir):
    """The two value-case artifact paths under ``out_dir``."""
    paths = {}
    for key in _VALUE_CASE_NAMES:
        paths[key] = os.path.join(out_dir, _VALUE_CASE_NAMES[key])
    return paths


def build_value_case_command(interpreter, opts, out_dir):
    """argv for a value-case run: ``python -m steelreuse.value_case_cli --donor ...``.

    ``opts`` must carry ``donor``; market-price keys are all optional (CLI defaults apply when
    absent). No demand model is needed -- the whole point of the feature.
    """
    paths = value_case_paths(out_dir)
    cmd = [interpreter, "-m", "steelreuse.value_case_cli",
           "--donor", opts["donor"],
           "--out-writeback", paths["writeback"],
           "--out-json", paths["results"],
           "--out-csv", paths["csv"]]
    for key, flag in (("scrap_price", "--scrap-price"),
                      ("reclaimed_price", "--reclaimed-price"),
                      ("co2_price", "--co2-price"),
                      ("knockdown", "--knockdown")):
        val = opts.get(key)
        if val is not None:
            cmd.append(flag)
            cmd.append(str(val))
    if opts.get("pda"):
        cmd.extend(["--pda", opts["pda"]])
    if opts.get("include_unverified"):
        cmd.append("--include-unverified")
    if opts.get("include_unmapped"):
        cmd.append("--include-unmapped")
    return cmd


def run_value_case(interpreter, opts, out_dir):
    """Run the per-member business-case generator synchronously; return the same dict as run_match.

    ``opts`` needs only ``donor`` -- no demand model required. Market-price opts are optional.
    Safe to call on a background thread (no Revit API touched here).
    """
    return _run_logged(build_value_case_command(interpreter, opts, out_dir), out_dir,
                       value_case_paths(out_dir), "value_case.log")


# Default CSS for the report buttons' standalone HTML (Results / Problems / PDA QA). The bare
# problem/PDA fragments carry no <style>, so this gives them a readable table; the results view ships
# its own scoped <style>, which still applies once dropped inside <body>.
_REPORT_CSS = (
    "<style>"
    "body{font-family:'Segoe UI',Arial,sans-serif;margin:1.2em;color:#222;}"
    "table{border-collapse:collapse;width:100%;font-size:0.93em;margin-top:0.5em;}"
    "th,td{border:1px solid #ccc;padding:3px 6px;text-align:left;}"
    "th{background:#eee;}h2{margin:0.3em 0;}.review{color:#c33;font-weight:bold;}"
    "</style>"
)


def open_html_report(out_path, title, body_html):
    """Write ``body_html`` as a self-contained HTML document to ``out_path``, open it in the default
    browser, and return ``out_path``.

    The report buttons call this instead of relying on the pyRevit output window: under Revit 2026
    that window's WebView renders blank -- even plain ``print_md`` never appears -- so the only
    reliable channel is a file on disk opened in the system browser (the same ``os.startfile`` route
    as Run Match's "Open report" footer). Best-effort launch: if the browser cannot be opened the
    file is still written, so the caller can print the path as a fallback link.
    """
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    doc = ("<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>" + title + "</title>"
           + _REPORT_CSS + "</head><body>" + body_html + "</body></html>")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(doc)
    try:
        os.startfile(out_path)  # Windows: hand the file to the default browser
    except Exception:  # noqa: BLE001 -- non-Windows dev box, or no file association
        try:
            webbrowser.open("file:///" + out_path.replace("\\", "/"))
        except Exception:  # noqa: BLE001 -- file is on disk regardless; caller prints the path
            pass
    return out_path
