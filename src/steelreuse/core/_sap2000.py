"""Lazy SAP2000 OAPI connection — the *only* module in the package that imports ``comtypes``.

Everything here is optional and OFF the default path. The public surface is deliberately tiny:

  * :class:`Sap2000Unavailable` — the single exception every failure mode collapses to (comtypes not
    installed, SAP2000 not registered/licensed, the Educational model-size cap, any COM error), so
    callers can ``except Sap2000Unavailable`` and fall back to the analytic path exactly as they do
    for a missing PyNite.
  * :func:`connect_sap2000` — start an instance, return a live handle, or raise.
  * :func:`sap2000_session` — a context manager wrapping the above that guarantees the SAP2000
    application is closed even if the body raises.

Units are initialised to **N, mm, °C** (SAP2000 ``eUnits`` value 9) to match the project's internal
N/mm convention, so no force/length conversion is needed at the boundary.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

# SAP2000 eUnits enumeration: N_mm_C = 9 (force N, length mm, temp °C).
_UNITS_N_MM_C = 9
_NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW — keep tasklist/taskkill from flashing a console


class Sap2000Unavailable(RuntimeError):
    """SAP2000 or its COM bridge could not be reached. The caller falls back to the analytic path."""


def _sap_pids() -> set[int]:
    """PIDs of every running ``SAP2000.exe`` (best-effort; empty set if tasklist is unavailable)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SAP2000.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW).stdout
    except Exception:  # noqa: BLE001 - process listing must never raise into the caller
        return set()
    pids: set[int] = set()
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) >= 2:
            with suppress(ValueError):
                pids.add(int(parts[1].strip('"')))
    return pids


def _new_sap_pids(before: set[int], tries: int = 6) -> set[int]:
    """The SAP2000 PID(s) that appeared since the ``before`` snapshot (the instance(s) we spawned)."""
    for _ in range(tries):
        new = _sap_pids() - before
        if new:
            return new
        time.sleep(0.5)
    return set()


def _kill_pids(pids: set[int]) -> None:
    """Force-terminate specific SAP2000 PIDs (only ones we started) — the reliable cleanup."""
    for pid in pids:
        with suppress(Exception):
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, timeout=10, creationflags=_NO_WINDOW)


@dataclass
class SapHandle:
    """A live SAP2000 connection: the application object, its ``SapModel``, and the PID(s) we spawned
    (so cleanup can force-kill them if the OAPI ``ApplicationExit`` fails to close them)."""

    sap_object: object
    model: object
    pids: set[int] = field(default_factory=set)


def connect_sap2000(visible: bool = False) -> SapHandle:
    """Start a **fresh, hidden** SAP2000 instance and return a :class:`SapHandle` (blank model, N/mm).

    We deliberately create our *own* instance rather than attach to a running one (attaching and
    blanking the model would wipe a model the user has open). ``visible=False`` (default) keeps the
    GUI off so automation runs don't pile up visible windows; the trial's concurrent-instance cap
    still applies, so every instance **must** be closed — always drive solves through
    :func:`sap2000_session`, which guarantees that even on error.

    Raises :class:`Sap2000Unavailable` for *any* failure (comtypes import, COM creation, a non-zero
    OAPI code, or a licence/registration problem).
    """
    try:
        import comtypes.client  # noqa: PLC0415 - intentionally lazy; isolates the only COM import
    except ImportError as exc:
        raise Sap2000Unavailable(
            "comtypes is not installed — install the optional extra with "
            "`pip install -e '.[sap2000]'` (Windows only)"
        ) from exc

    before = _sap_pids()          # SAP2000 already running (the user's) — never touch these
    sap_object = None
    spawned: set[int] = set()
    try:
        helper = comtypes.client.CreateObject("SAP2000v1.Helper")
        helper = helper.QueryInterface(comtypes.gen.SAP2000v1.cHelper)
        sap_object = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
        # ApplicationStart(Units, Visible, FileName): start hidden in N/mm with no model file.
        if sap_object.ApplicationStart(_UNITS_N_MM_C, visible, "") != 0:
            raise Sap2000Unavailable("SAP2000 ApplicationStart returned a non-zero code")
        spawned = _new_sap_pids(before)   # the instance we just started (for guaranteed teardown)
        model = sap_object.SapModel
        if model.InitializeNewModel(_UNITS_N_MM_C) != 0:
            raise Sap2000Unavailable("SAP2000 InitializeNewModel returned a non-zero code")
        if model.File.NewBlank() != 0:
            raise Sap2000Unavailable("SAP2000 File.NewBlank returned a non-zero code")
    except Sap2000Unavailable:
        _safe_exit(sap_object, spawned)
        raise
    except Exception as exc:  # noqa: BLE001 - any COM/licence error collapses to one clean type
        _safe_exit(sap_object, spawned)
        raise Sap2000Unavailable(f"could not start SAP2000 via the OAPI ({exc})") from exc

    return SapHandle(sap_object=sap_object, model=model, pids=spawned)


def _safe_exit(sap_object, pids: set[int] | None = None) -> None:
    """Close the SAP2000 application we started. Tries the polite OAPI ``ApplicationExit`` first, then
    **force-kills** any of *our* spawned PIDs that survive it — the OAPI close is unreliable in hidden
    mode and after a failed analysis, which is how instances pile up against the trial's cap. Only PIDs
    we started are killed; a SAP2000 the user opened by hand is never in this set."""
    if sap_object is not None:
        with suppress(Exception):  # cleanup must never mask the original error
            sap_object.ApplicationExit(False)
    if pids:
        time.sleep(1.0)                       # give ApplicationExit a moment to take effect
        survivors = _sap_pids() & pids
        if survivors:
            _kill_pids(survivors)


@contextmanager
def sap2000_session(visible: bool = False, watchdog_s: float = 180.0):
    """Context manager yielding a live ``SapModel``; **always** closes (and, if needed, force-kills)
    the instance on exit, so a solve never leaks against the trial's concurrent-instance cap.

    A **watchdog** force-kills our spawned instance after ``watchdog_s`` seconds: a COM call that
    deadlocks (e.g. a hidden modal dialog) cannot otherwise be interrupted, so the timer guarantees the
    process dies, the blocked call raises, and control returns rather than hanging forever. Set
    ``watchdog_s=0`` to disable."""
    import threading  # noqa: PLC0415 - only the optional SAP2000 path needs this

    handle = connect_sap2000(visible=visible)
    timer = None
    if handle.pids and watchdog_s:
        timer = threading.Timer(watchdog_s, _kill_pids, args=(handle.pids,))
        timer.daemon = True
        timer.start()
    try:
        yield handle.model
    finally:
        if timer is not None:
            timer.cancel()
        _safe_exit(handle.sap_object, handle.pids)
