"""Locate data files bundled inside the installed package.

These resolve the same way from a source checkout or an installed wheel, because the ``data/``
directory ships inside the ``steelreuse`` package (see ``[tool.hatch.build.targets.wheel]``).
"""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
SECTIONS_DIR = DATA_DIR / "sections"
SAMPLES_DIR = DATA_DIR / "samples"


def sample_path(name: str) -> Path:
    """Return the path to a bundled sample model, e.g. ``sample_path("donor.json")``."""
    p = SAMPLES_DIR / name
    if not p.exists():
        available = ", ".join(sorted(q.name for q in SAMPLES_DIR.glob("*.json"))) or "none"
        raise FileNotFoundError(f"bundled sample {name!r} not found (available: {available})")
    return p
