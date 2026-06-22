"""Survey authoring: export a context-rich PDA template, and import filled surveys from many tools
and formats (CSV / Excel / JSON) onto donor members.

Pure (stdlib only, except the optional openpyxl path for .xlsx), so it is fully unit-tested and runs
in the headless engine the pyRevit Import-Survey button shells out to. Reuses the audit field set and
value coercion; the importer is deliberately format- and tool-flexible (header aliases + value
normalisation + multi-key matching) rather than one adapter per tool.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

# Context columns (prefilled from the model) then audit columns (blank, for the surveyor to fill).
_CONTEXT_COLUMNS = ["unique_id", "mark", "id", "level", "role", "raw_section",
                    "length_mm", "recoverable_length_mm", "start_xyz", "end_xyz"]
_AUDIT_COLUMNS = ["condition_grade", "verification_status", "knockdown", "defects",
                  "connection_type", "connection_condition", "deconstructability"]
SURVEY_COLUMNS = _CONTEXT_COLUMNS + _AUDIT_COLUMNS


def survey_template_rows(model) -> list[dict]:
    """One row per donor member: context prefilled, audit columns blank."""
    rows = []
    for m in model.members:
        rl = getattr(m, "recoverable_length_mm", None)
        rows.append({
            "unique_id": m.unique_id or "",
            "mark": m.mark or "",
            "id": m.id,
            "level": m.level or "",
            "role": m.role,
            "raw_section": m.raw_section,
            "length_mm": m.length_mm,
            "recoverable_length_mm": rl if rl is not None else m.length_mm,
            "start_xyz": ";".join(str(x) for x in (m.start_xyz or [])),
            "end_xyz": ";".join(str(x) for x in (m.end_xyz or [])),
            **{c: "" for c in _AUDIT_COLUMNS},
        })
    return rows


def survey_template_csv(model) -> str:
    """Serialise the template rows to CSV with the fixed SURVEY_COLUMNS order."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=SURVEY_COLUMNS)
    w.writeheader()
    for row in survey_template_rows(model):
        w.writerow(row)
    return buf.getvalue()


_NUMBER_FIELDS = ("knockdown", "recoverable_length_mm")

_VERIFICATION_SYNONYMS = {
    "mill_cert": ("mill cert", "mill certificate", "cert", "certificate", "mtc"),
    "coupon_tested": ("coupon", "coupon test", "coupon tested", "tested", "tensile test"),
    "documented": ("documented", "drawings", "records", "as-built", "design records"),
    "visual_only": ("visual", "visual only", "assumed", "estimated", "era"),
    "unverified": ("unverified", "none", "unknown", "n/a", ""),
}
_CONDITION_SYNONYMS = {
    "A": ("a", "good", "as-new", "new", "excellent", "sound"),
    "B": ("b", "minor", "light", "light corrosion", "cosmetic"),
    "C": ("c", "significant", "section loss", "moderate", "deformed"),
    "D": ("d", "poor", "unsuitable", "bad", "severe", "heavy corrosion"),
}
_CONNECTION_SYNONYMS = {
    "bolted": ("bolted", "pinned", "simple", "shear", "bolt"),
    "welded": ("welded", "weld", "moment", "fixed"),
    "riveted": ("riveted", "rivet"),
}


def _match_synonym(text, table, default=None):
    low = text.strip().lower()
    for canonical, words in table.items():
        if low == canonical.lower() or low in words:
            return canonical
    return default


def normalize_survey_value(field, raw):
    """Coerce a raw survey cell to the stored form, mapping common synonyms; None when unset.

    Numbers -> float (None if unparseable). condition/verification/connection map through synonym
    tables, falling back to the canonicalised text (UPPER for condition, lower otherwise) so an
    unrecognised but plausible value is preserved for the audit layer rather than dropped.
    """
    text = (raw or "").strip()
    if field in _NUMBER_FIELDS:
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if field == "verification_status":
        return _match_synonym(text, _VERIFICATION_SYNONYMS, default=(text.lower() or "unverified"))
    if field == "condition_grade":
        if not text:
            return None
        return _match_synonym(text, _CONDITION_SYNONYMS, default=text.upper())
    if field == "connection_type":
        if not text:
            return None
        return _match_synonym(text, _CONNECTION_SYNONYMS, default="unknown")
    return text or None


# Source header (lowercased, separators stripped) -> our field name.
_HEADER_ALIASES = {
    # key
    "uniqueid": "unique_id", "unique_id": "unique_id", "guid": "unique_id",
    "globalid": "unique_id", "ifcguid": "unique_id",
    "id": "id", "elementid": "id",
    "mark": "mark",
    # audit
    "condition": "condition_grade", "conditiongrade": "condition_grade", "grade": "condition_grade",
    "state": "condition_grade",
    "verification": "verification_status", "verificationstatus": "verification_status",
    "basis": "verification_status", "gradebasis": "verification_status", "cert": "verification_status",
    "certification": "verification_status",
    "knockdown": "knockdown",
    "recoverablelength": "recoverable_length_mm", "recoverablelengthmm": "recoverable_length_mm",
    "defects": "defects", "notes": "defects",
    "connection": "connection_type", "connectiontype": "connection_type", "joint": "connection_type",
    "fixity": "connection_type",
    "connectioncondition": "connection_condition", "jointcondition": "connection_condition",
    "deconstructability": "deconstructability", "deconstruction": "deconstructability",
}

_IMPORTABLE_FIELDS = ("unique_id", "id", "mark", "condition_grade", "verification_status",
                      "knockdown", "recoverable_length_mm", "defects",
                      "connection_type", "connection_condition", "deconstructability")


def _canon_header(name):
    return "".join(ch for ch in (name or "").strip().lower() if ch.isalnum())


def _map_header(name, col_map):
    if col_map and name in col_map:
        return col_map[name]
    return _HEADER_ALIASES.get(_canon_header(name))


def _read_rows(path):
    """Read a survey file into a list of {source_header: cell_string} dicts (CSV / JSON / .xlsx)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("members", data.get("rows", []))
        return [{k: ("" if v is None else str(v)) for k, v in row.items()} for row in rows]
    if suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("reading .xlsx needs the optional 'xlsx' extra: "
                               "pip install steelreuse[xlsx]") from e
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        headers = [str(h) if h is not None else "" for h in next(it, [])]
        out = []
        for vals in it:
            out.append({headers[i]: ("" if v is None else str(v))
                        for i, v in enumerate(vals) if i < len(headers)})
        return out
    # default: CSV (also handles .tsv via the sniffer-free DictReader on commas)
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def resolve_key(record):
    """The element key for a parsed record: unique_id -> id -> mark, first non-empty."""
    for field in ("unique_id", "id", "mark"):
        val = (record.get(field) or "").strip()
        if val:
            return val
    return None


def load_survey(path, col_map=None):
    """Read a survey file (CSV/Excel/JSON) into ``{element_key: {field: coerced_value}}``.

    Columns are mapped to our fields by ``col_map`` (override) then the alias table; cell values are
    normalised via :func:`normalize_survey_value`. Rows without a resolvable key are skipped.
    """
    out = {}
    for raw_row in _read_rows(path):
        record = {}
        for src_header, cell in raw_row.items():
            field = _map_header(src_header, col_map)
            if field is None or field not in _IMPORTABLE_FIELDS:
                continue
            if field in ("unique_id", "id", "mark"):
                record[field] = (cell or "").strip()
            else:
                value = normalize_survey_value(field, cell)
                if value is not None:
                    record[field] = value
        key = resolve_key(record)
        if not key:
            continue
        # Don't store the key columns themselves as audit fields.
        record.pop("unique_id", None)
        record.pop("id", None)
        record.pop("mark", None)
        out[key] = record
    return out
