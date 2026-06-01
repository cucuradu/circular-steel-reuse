"""Extraction inventory — a "what's in this building" summary from an extracted model.

Unlike the carbon passport (which needs catalog section properties), this works on *any* extracted
model, mapped or not. It is the rawest form of a pre-demolition / urban-mining audit: counts, section
types, total linear length, and a per-level breakdown, straight from the extracted geometry.

If a catalog is supplied it also reports how many members map vs. land in the ``unknown`` bucket, and
adds mass/embodied-carbon for the mapped subset (honest: unknown sections are listed but not costed).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .core.carbon import CarbonFactor, build_passport, load_factors
from .core.sections import SectionProps, resolve_members
from .schema import ExtractedModel


@dataclass
class SectionRow:
    raw_section: str
    count: int
    total_length_mm: float
    mapped_to: str | None  # canonical name if mapped, else None


@dataclass
class Inventory:
    model_name: str
    kind: str
    n_members: int
    n_beams: int
    n_columns: int
    total_length_mm: float
    by_section: list[SectionRow]
    by_level: dict[str, int]
    n_mapped: int = 0
    n_unknown: int = 0
    total_mass_kg: float = 0.0
    total_new_co2_kg: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def total_length_m(self) -> float:
        return self.total_length_mm / 1000.0


def build_inventory(
    model: ExtractedModel,
    catalog: dict[str, SectionProps] | None = None,
    factors: dict[str, CarbonFactor] | None = None,
) -> Inventory:
    members = model.members
    beams = [m for m in members if m.role == "beam"]
    columns = [m for m in members if m.role == "column"]

    # If a catalog is given, resolve sections so we can tag mapped vs unknown and cost the mapped ones.
    n_mapped = n_unknown = 0
    if catalog is not None:
        report = resolve_members(members, catalog)
        n_mapped = len(report.mapped) + len(report.fuzzy)
        n_unknown = len(report.unknown)

    sec_count: dict[str, int] = defaultdict(int)
    sec_length: dict[str, float] = defaultdict(float)
    sec_mapped: dict[str, str | None] = {}
    for m in members:
        key = m.raw_section or "(blank)"
        sec_count[key] += 1
        sec_length[key] += m.length_mm or 0.0
        sec_mapped[key] = m.section  # canonical name or None
    by_section = sorted(
        (SectionRow(k, sec_count[k], round(sec_length[k], 1), sec_mapped[k]) for k in sec_count),
        key=lambda r: r.count, reverse=True,
    )

    by_level: dict[str, int] = defaultdict(int)
    for m in members:
        by_level[m.level or "(no level)"] += 1

    warnings: list[str] = []
    zero_len = sum(1 for m in members if not m.length_mm)
    if zero_len:
        warnings.append(f"{zero_len} member(s) have no length and are excluded from length totals")

    inv = Inventory(
        model_name=model.model_name or "(unnamed)",
        kind=model.kind,
        n_members=len(members),
        n_beams=len(beams),
        n_columns=len(columns),
        total_length_mm=round(sum(m.length_mm or 0.0 for m in members), 1),
        by_section=by_section,
        by_level=dict(sorted(by_level.items())),
        n_mapped=n_mapped,
        n_unknown=n_unknown,
    )

    if catalog is not None:
        passport = build_passport(members, catalog, factors or load_factors())
        inv.total_mass_kg = round(passport.total_mass_kg, 1)
        inv.total_new_co2_kg = round(passport.total_new_kgco2e, 1)
        if n_unknown:
            warnings.append(
                f"{n_unknown} member(s) have sections not in the catalog -> listed but not costed "
                "(mass/CO2 covers only the mapped subset)"
            )
    inv.warnings = warnings
    return inv


def render_inventory_text(inv: Inventory) -> str:
    lines = [
        f"Inventory: {inv.model_name}  ({inv.kind})",
        f"  members: {inv.n_members}  (beams {inv.n_beams}, columns {inv.n_columns})",
        f"  total steel length: {inv.total_length_m:.1f} m",
    ]
    if inv.n_mapped or inv.n_unknown:
        lines.append(f"  catalog: {inv.n_mapped} mapped, {inv.n_unknown} unknown")
    if inv.total_mass_kg:
        lines.append(f"  mapped mass: {inv.total_mass_kg:.0f} kg  "
                     f"(new-build embodied CO2 ~ {inv.total_new_co2_kg:.0f} kg)")
    lines.append("  top sections:")
    for r in inv.by_section[:12]:
        tag = f"-> {r.mapped_to}" if r.mapped_to else "(unknown)"
        lines.append(f"    {r.count:>4}x  {r.raw_section[:42]:<42} {r.total_length_mm/1000:7.1f} m  {tag}")
    for w in inv.warnings:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Extraction Inventory</title>
<style>
 body{font-family:system-ui,Arial,sans-serif;margin:2rem;color:#1a1a1a;max-width:900px}
 h1{font-size:1.4rem} .kpis{display:flex;gap:1.5rem;flex-wrap:wrap;margin:1rem 0}
 .kpi{background:#f3f6f4;border-radius:10px;padding:.8rem 1.1rem} .kpi b{display:block;font-size:1.5rem}
 table{border-collapse:collapse;width:100%;margin:1rem 0} th,td{border:1px solid #ddd;padding:.35rem .6rem;font-size:.88rem;text-align:left}
 th{background:#eef2ef} .unk{color:#a15c00} .warn{background:#fff7e6;padding:.6rem;border-radius:8px;font-size:.85rem}
</style></head><body>
<h1>Pre-demolition Steel Inventory — {{name}} ({{kind}})</h1>
<div class="kpis">
 <div class="kpi"><b>{{n_members}}</b>members</div>
 <div class="kpi"><b>{{n_beams}}</b>beams</div>
 <div class="kpi"><b>{{n_columns}}</b>columns</div>
 <div class="kpi"><b>{{length_m}}</b>m total length</div>
 {{mapped_kpi}}
</div>
<h2>By section</h2>
<table><tr><th>Section (raw Revit type)</th><th>Count</th><th>Total length (m)</th><th>Catalog match</th></tr>
{{section_rows}}
</table>
<h2>By level</h2>
<table><tr><th>Level</th><th>Members</th></tr>
{{level_rows}}
</table>
{{warnings}}
</body></html>"""


def render_inventory_html(inv: Inventory) -> str:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    section_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{:.1f}</td><td class='{}'>{}</td></tr>".format(
            esc(r.raw_section), r.count, r.total_length_mm / 1000.0,
            "" if r.mapped_to else "unk", r.mapped_to or "unknown",
        )
        for r in inv.by_section
    )
    level_rows = "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in inv.by_level.items())
    mapped_kpi = ""
    if inv.n_mapped or inv.n_unknown:
        mapped_kpi = (f"<div class='kpi'><b>{inv.n_mapped}/{inv.n_members}</b>mapped to catalog</div>")
    warnings = ""
    if inv.warnings:
        warnings = "<div class='warn'>" + "<br>".join("⚠ " + esc(w) for w in inv.warnings) + "</div>"
    return (_HTML
            .replace("{{name}}", esc(inv.model_name)).replace("{{kind}}", esc(inv.kind))
            .replace("{{n_members}}", str(inv.n_members)).replace("{{n_beams}}", str(inv.n_beams))
            .replace("{{n_columns}}", str(inv.n_columns))
            .replace("{{length_m}}", f"{inv.total_length_m:.1f}")
            .replace("{{mapped_kpi}}", mapped_kpi)
            .replace("{{section_rows}}", section_rows).replace("{{level_rows}}", level_rows)
            .replace("{{warnings}}", warnings))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse
    from pathlib import Path

    from .core.sections import load_catalog

    ap = argparse.ArgumentParser(description="Summarize an extracted model as a steel inventory")
    ap.add_argument("json", help="extracted model JSON (donor or demand)")
    ap.add_argument("--out", default="reports/inventory.html", help="output HTML path")
    ap.add_argument("--no-catalog", action="store_true", help="skip catalog mapping/costing")
    args = ap.parse_args(argv)

    model = ExtractedModel.load(args.json)
    catalog = None if args.no_catalog else load_catalog()
    inv = build_inventory(model, catalog)
    print(render_inventory_text(inv))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_inventory_html(inv), encoding="utf-8")
    print(f"\nInventory report -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
