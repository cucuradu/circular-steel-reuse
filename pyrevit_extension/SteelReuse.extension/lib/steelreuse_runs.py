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


def record_run(history_dir, name, params_label, results_path, run_id=None, status_path=None,
               evidence_path=None, mismatch_path=None):
    """Copy ``results_path`` into the history under a fresh id, append to the manifest, return entry.

    When ``status_path`` (the run's apply-matches JSON, donor/demand per-element status) is given and
    exists, it is archived too as ``status_<id>.json`` and recorded under ``status_file`` so the run
    can be re-applied to the model later (see :func:`load_run_status`). Runs recorded before this was
    added simply have no ``status_file`` and are not re-applicable.

    Likewise, when given and present, the signable per-run **evidence package** (Roadmap §1.1) and the
    donor **mismatch log** (§1.2) are archived as ``evidence_<id>.json`` / ``mismatch_<id>.csv`` and
    recorded under ``evidence_file`` / ``mismatch_file`` -- so a saved run carries them too and the
    Results window's "Open folder" / "Open evidence" can reach them (they live in the live run output
    folder otherwise, not the history holder).
    """
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
    if status_path and os.path.isfile(status_path):
        status_fname = "status_" + rid + ".json"
        shutil.copyfile(status_path, os.path.join(history_dir, status_fname))
        entry["status_file"] = status_fname
    # Archive the evidence package + mismatch log alongside, keyed by id, when the engine wrote them.
    for src, prefix, ext, key in (
            (evidence_path, "evidence_", ".json", "evidence_file"),
            (mismatch_path, "mismatch_", ".csv", "mismatch_file")):
        if src and os.path.isfile(src):
            dest = prefix + rid + ext
            shutil.copyfile(src, os.path.join(history_dir, dest))
            entry[key] = dest
    manifest = _read_raw(history_dir)
    manifest.append(entry)
    _save_manifest(history_dir, manifest)
    return entry


def run_artifact_path(history_dir, run_id, key):
    """Absolute path to an archived artifact for a run id, or None.

    ``key`` is a manifest field naming a file: ``file`` (results.json), ``status_file``,
    ``evidence_file`` or ``mismatch_file``. Returns None when the run or that artifact is absent.
    """
    for r in _read_raw(history_dir):
        if r.get("id") == run_id:
            fname = r.get(key)
            if not fname:
                return None
            path = os.path.join(history_dir, fname)
            return path if os.path.isfile(path) else None
    return None


def delete_run(history_dir, run_id):
    """Drop a run from the manifest and delete its file. True if it existed."""
    manifest = _read_raw(history_dir)
    kept = [r for r in manifest if r.get("id") != run_id]
    if len(kept) == len(manifest):
        return False
    _save_manifest(history_dir, kept)
    for r in manifest:
        if r.get("id") == run_id:
            for key in ("file", "status_file"):
                if r.get(key):
                    try:
                        os.remove(os.path.join(history_dir, r[key]))
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


def load_run_status(history_dir, run_id):
    """The archived apply-matches status dict (donor/demand) for a run id, or None.

    None when the run predates status archiving or its file is missing -- the caller should tell the
    user that run can't be applied and offer to re-run it.
    """
    for r in _read_raw(history_dir):
        if r.get("id") == run_id:
            status_file = r.get("status_file")
            if not status_file:
                return None
            path = os.path.join(history_dir, status_file)
            if not os.path.isfile(path):
                return None
            with open(path) as handle:
                return json.load(handle)
    return None
