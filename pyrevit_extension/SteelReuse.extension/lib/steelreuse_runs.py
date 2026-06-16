# -*- coding: utf-8 -*-
"""Run history for the SteelReuse Compare Runs tool: save each match run's results.json under a name,
and list / delete / load them.

Stdlib only, IronPython-safe (no f-strings). No Revit, so it is unit-tested headless. The manifest is
``runs.json`` in the history folder, holding ``[{id, name, params_label, timestamp, file}, ...]``.
"""

import json
import os
import shutil
import time

_MANIFEST = "runs.json"


def _manifest_path(history_dir):
    return os.path.join(history_dir, _MANIFEST)


def _read_raw(history_dir):
    """The raw manifest list (oldest first, as stored); [] when there is none."""
    path = _manifest_path(history_dir)
    if not os.path.isfile(path):
        return []
    with open(path) as handle:
        return json.load(handle)


def _save_manifest(history_dir, runs):
    with open(_manifest_path(history_dir), "w") as handle:
        json.dump(runs, handle, indent=2)


def load_runs(history_dir):
    """Saved runs newest-first; entries whose results file is gone are skipped."""
    out = [r for r in _read_raw(history_dir)
           if os.path.isfile(os.path.join(history_dir, r.get("file", "")))]
    return list(reversed(out))


def record_run(history_dir, name, params_label, results_path, run_id=None):
    """Copy ``results_path`` into the history under a fresh id, append to the manifest, return entry."""
    if not os.path.isdir(history_dir):
        os.makedirs(history_dir)
    rid = run_id or time.strftime("%Y%m%d-%H%M%S")
    existing = set(r.get("id") for r in _read_raw(history_dir))
    base, n = rid, 1
    while rid in existing:
        rid = base + "-" + str(n)
        n += 1
    fname = "run_" + rid + ".json"
    shutil.copyfile(results_path, os.path.join(history_dir, fname))
    entry = {"id": rid, "name": name or "run", "params_label": params_label or "",
             "timestamp": rid, "file": fname}
    manifest = _read_raw(history_dir)
    manifest.append(entry)
    _save_manifest(history_dir, manifest)
    return entry


def delete_run(history_dir, run_id):
    """Drop a run from the manifest and delete its file. True if it existed."""
    manifest = _read_raw(history_dir)
    kept = [r for r in manifest if r.get("id") != run_id]
    if len(kept) == len(manifest):
        return False
    _save_manifest(history_dir, kept)
    for r in manifest:
        if r.get("id") == run_id:
            try:
                os.remove(os.path.join(history_dir, r.get("file", "")))
            except OSError:
                pass
    return True


def load_run_data(history_dir, run_id):
    """The saved results.json dict for a run id, or None."""
    for r in _read_raw(history_dir):
        if r.get("id") == run_id:
            with open(os.path.join(history_dir, r["file"])) as handle:
                return json.load(handle)
    return None
