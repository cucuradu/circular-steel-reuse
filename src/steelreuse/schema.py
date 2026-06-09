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


class ExtractionError(ValueError):
    """A model file is missing, not valid JSON, or does not match the extraction schema.

    Raised at the input boundary so the CLI can report a clear message instead of a traceback.
    """


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
        """Load and validate an extraction file, raising :class:`ExtractionError` on bad input."""
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise ExtractionError(f"extraction file not found: {p}") from e
        except OSError as e:
            raise ExtractionError(f"could not read {p}: {e}") from e
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise ExtractionError(f"{p} is not valid JSON: {e}") from e
        if not isinstance(raw, dict):
            raise ExtractionError(
                f"{p}: expected a JSON object at the top level, got {type(raw).__name__}"
            )
        members = raw.get("members", [])
        if not isinstance(members, list):
            raise ExtractionError(f"{p}: 'members' must be a list, got {type(members).__name__}")
        for i, m in enumerate(members):
            if not isinstance(m, dict):
                raise ExtractionError(f"{p}: members[{i}] must be an object, got {type(m).__name__}")
            if "id" not in m:
                raise ExtractionError(f"{p}: members[{i}] is missing the required 'id' field")
            for fld in ("length_mm",):
                if fld in m and not isinstance(m[fld], (int, float)):
                    raise ExtractionError(
                        f"{p}: members[{i}] ({m['id']}).{fld} must be a number, "
                        f"got {type(m[fld]).__name__}"
                    )
            spans = m.get("spans_mm")
            if spans is not None and (
                not isinstance(spans, list) or any(not isinstance(s, (int, float)) for s in spans)
            ):
                raise ExtractionError(f"{p}: members[{i}] ({m['id']}).spans_mm must be a list of numbers")
        return cls.from_dict(raw)
