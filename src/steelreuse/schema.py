"""Shared JSON schema for models extracted from Revit (or IFC/Speckle).

The pyRevit extractor writes files matching this schema; the rest of the CPython pipeline reads
them. Standard-library only on purpose: imported on both sides of the Revit boundary.

Key distinction (see plan, "Continuous-beam handling"):
  * supply (donor) members carry a single physical ``length_mm`` = the reusable stock length;
  * demand members carry ``spans_mm`` = the structural spans after splitting at supports.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

# Internal canonical units once parsed: forces in N, lengths in mm (so stress in N/mm^2 = MPa).
UNITS = "extract:mm | internal:N,mm"

ROLES = ("beam", "column", "brace", "unknown")
KINDS = ("donor", "demand")


@dataclass
class ExtractedMember:
    """One structural steel member as read from the model.

    ``section`` is left ``None`` by the extractor and filled later by the mapping layer
    (:mod:`steelreuse.core.sections`); ``raw_section`` always keeps the original Revit type name.
    """

    id: str
    role: str = "unknown"          # one of ROLES
    category: str = ""             # raw Revit category, e.g. "Structural Framing"
    raw_section: str = ""          # original Revit family/type name, e.g. "IPE 300" / "HE 300 A"
    section: str | None = None     # canonical catalog name once mapped, e.g. "IPE300"
    material_grade: str | None = None  # e.g. "S275"; None if unknown
    level: str | None = None
    length_mm: float = 0.0         # physical member length (== reusable stock length for supply)
    spans_mm: list[float] = field(default_factory=list)  # structural spans (demand)
    start_xyz: list[float] | None = None
    end_xyz: list[float] | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            self.role = "unknown"
        if not self.spans_mm and self.length_mm:
            self.spans_mm = [self.length_mm]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ExtractedMember:
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ExtractedModel:
    """Envelope written to ``donor.json`` / ``demand.json``."""

    kind: str                      # one of KINDS
    members: list[ExtractedMember] = field(default_factory=list)
    source: str = "pyrevit"        # pyrevit | ifc | speckle | sample
    units: str = UNITS
    schema_version: int = SCHEMA_VERSION
    model_name: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["members"] = [m.to_dict() for m in self.members]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ExtractedModel:
        members = [ExtractedMember.from_dict(m) for m in d.get("members", [])]
        return cls(
            kind=d.get("kind", "donor"),
            members=members,
            source=d.get("source", "pyrevit"),
            units=d.get("units", UNITS),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            model_name=d.get("model_name", ""),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ExtractedModel:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
