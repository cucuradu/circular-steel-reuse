"""Sensitivity & uncertainty study of the headline CO2-saved figure.

The report books **one** number — "X kg CO2e saved" — under a fixed set of assumptions (knockdown,
EN 1990 partial factors, the floor pressures, the end-of-life counterfactual). A thesis examiner's
reliable question is: *how much does that number move when those assumptions move?* This module answers
it two ways, by **re-running the existing pipeline** and reading ``match.total_co2_saved_kg`` — no number
is recomputed here, so the anti-arithmetic discipline (docs/DESIGN_PRINCIPLES.md rule 1) is preserved.

  * **Tornado (one-at-a-time):** vary each driver across a documented low/high range with everything
    else at baseline, and rank the drivers by the swing they cause. Shows *which* assumption matters.
  * **Monte Carlo (optional):** sample all numeric drivers together from their ranges and report a
    CO2-saved confidence band (P5-P50-P95) around the point estimate. Shows the *combined* uncertainty.

Everything defaults OFF in the main pipeline; this is an extra analysis the user opts into via the
``steelreuse-sensitivity`` console command. The chart needs matplotlib (the ``[analysis]`` extra); the
sweep and CSV are standard-library only, so the logic is testable without heavy deps.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .core.loads import AreaLoadModel
from .core.sections import SectionProps, load_default_catalog
from .pipeline import run_pipeline


@dataclass
class RunParams:
    """The assumptions a single pipeline run is parameterized on (everything else stays at defaults)."""

    dead_kpa: float = 3.5
    live_kpa: float = 3.0
    gamma_g: float = 1.35
    gamma_q: float = 1.5
    knockdown: float = 1.0
    counterfactual: str = "none"   # none | recycling | rerolling

    def loads(self) -> AreaLoadModel:
        return AreaLoadModel(dead_kpa=self.dead_kpa, live_kpa=self.live_kpa,
                             gamma_g=self.gamma_g, gamma_q=self.gamma_q)


def evaluate(
    params: RunParams,
    donor: str,
    demand: str,
    catalog: dict[str, SectionProps] | None = None,
) -> float:
    """CO2 saved (kg) for one set of assumptions — a thin wrapper over :func:`run_pipeline`.

    Mirrors the CLI/report defaults (steel-only demand, cutting-stock on) so the figure matches the
    headline a user would see for the same model. The ``catalog`` is loaded once by the caller and
    threaded in to avoid re-reading the CSVs on every run of a sweep."""
    res = run_pipeline(
        donor, demand, loads=params.loads(), knockdown=params.knockdown,
        counterfactual=params.counterfactual, catalog=catalog,
        steel_only_demand=True, allow_cutting=True,
    )
    return res.match.total_co2_saved_kg


@dataclass
class Variant:
    """One evaluated point of a driver: a human label and the resulting CO2 saved."""

    label: str
    co2_saved_kg: float


@dataclass
class TornadoEntry:
    """A driver's effect: the variants evaluated, and the swing (max - min CO2) they produce."""

    driver: str
    variants: list[Variant]

    @property
    def low(self) -> Variant:
        return min(self.variants, key=lambda v: v.co2_saved_kg)

    @property
    def high(self) -> Variant:
        return max(self.variants, key=lambda v: v.co2_saved_kg)

    @property
    def swing(self) -> float:
        return self.high.co2_saved_kg - self.low.co2_saved_kg


@dataclass
class Driver:
    """A varied assumption: a label plus the alternative values to try, applied onto a baseline.

    ``apply`` writes one alternative value into a copy of the baseline :class:`RunParams`. ``values``
    are (label, value) pairs; the baseline value is included so the swing is measured around it.
    """

    key: str
    label: str
    values: Sequence[tuple[str, object]]
    apply: Callable[[RunParams, object], RunParams]


def _with(base: RunParams, **kw) -> RunParams:
    return RunParams(**{**base.__dict__, **kw})


def default_drivers(base: RunParams) -> list[Driver]:
    """The standard driver set with documented +/- ranges around the baseline.

    Ranges (rationale in docs/METHODOLOGY.md sensitivity section):
      * knockdown   0.70-1.00  — condition/verification derating of reclaimed f_y (audit layer).
      * dead/live   +/-20 %     — pre-sizing uncertainty in the floor pressures.
      * gamma_G     1.20-1.50   — EN 1990 permanent-action factor spread (incl. 6.10a/6.10b style).
      * gamma_Q     1.35-1.50   — EN 1990 variable-action factor spread.
      * counterfactual none/recycling/rerolling — the LCA end-of-life basis the saving is booked net of.
    """
    return [
        Driver("knockdown", "Reclaimed knockdown (f_y)",
               [("0.70", 0.70), ("1.00", 1.00)],
               lambda p, v: _with(p, knockdown=float(v))),
        Driver("dead_kpa", "Permanent load g_k",
               [("-20%", base.dead_kpa * 0.8), ("+20%", base.dead_kpa * 1.2)],
               lambda p, v: _with(p, dead_kpa=float(v))),
        Driver("live_kpa", "Imposed load q_k",
               [("-20%", base.live_kpa * 0.8), ("+20%", base.live_kpa * 1.2)],
               lambda p, v: _with(p, live_kpa=float(v))),
        Driver("gamma_g", "Partial factor gamma_G",
               [("1.20", 1.20), ("1.50", 1.50)],
               lambda p, v: _with(p, gamma_g=float(v))),
        Driver("gamma_q", "Partial factor gamma_Q",
               [("1.35", 1.35), ("1.50", 1.50)],
               lambda p, v: _with(p, gamma_q=float(v))),
        Driver("counterfactual", "EOL counterfactual",
               [("none", "none"), ("recycling", "recycling"), ("rerolling", "rerolling")],
               lambda p, v: _with(p, counterfactual=str(v))),
    ]


@dataclass
class MonteCarloBand:
    """The CO2-saved distribution from sampling all numeric drivers together."""

    samples: list[float]
    p5: float
    p50: float
    p95: float
    n: int


@dataclass
class SensitivityResult:
    baseline: RunParams
    baseline_co2_kg: float
    tornado: list[TornadoEntry]          # sorted by swing, descending
    monte_carlo: MonteCarloBand | None = None
    warnings: list[str] = field(default_factory=list)


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolated percentile of an already-sorted list (stdlib; no numpy)."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def run_tornado(
    donor: str,
    demand: str,
    base: RunParams | None = None,
    drivers: list[Driver] | None = None,
    catalog: dict[str, SectionProps] | None = None,
) -> tuple[float, list[TornadoEntry]]:
    """Evaluate every driver one-at-a-time and return the baseline CO2 + sorted tornado entries."""
    base = base or RunParams()
    catalog = catalog or load_default_catalog()
    drivers = drivers if drivers is not None else default_drivers(base)
    baseline_co2 = evaluate(base, donor, demand, catalog)
    entries: list[TornadoEntry] = []
    for d in drivers:
        variants = [Variant(label, evaluate(d.apply(base, value), donor, demand, catalog))
                    for label, value in d.values]
        entries.append(TornadoEntry(d.label, variants))
    entries.sort(key=lambda e: e.swing, reverse=True)
    return baseline_co2, entries


def run_monte_carlo(
    donor: str,
    demand: str,
    base: RunParams | None = None,
    n: int = 200,
    seed: int = 0,
    catalog: dict[str, SectionProps] | None = None,
) -> MonteCarloBand:
    """Sample the numeric drivers from their (uniform) ranges together and report the CO2 band.

    The counterfactual is held at the baseline (categorical, reported separately in the tornado);
    the numeric drivers use the same ranges as :func:`default_drivers`."""
    base = base or RunParams()
    catalog = catalog or load_default_catalog()
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n):
        p = RunParams(
            dead_kpa=rng.uniform(base.dead_kpa * 0.8, base.dead_kpa * 1.2),
            live_kpa=rng.uniform(base.live_kpa * 0.8, base.live_kpa * 1.2),
            gamma_g=rng.uniform(1.20, 1.50),
            gamma_q=rng.uniform(1.35, 1.50),
            knockdown=rng.uniform(0.70, 1.00),
            counterfactual=base.counterfactual,
        )
        samples.append(evaluate(p, donor, demand, catalog))
    s = sorted(samples)
    return MonteCarloBand(samples=samples, p5=_percentile(s, 5), p50=_percentile(s, 50),
                          p95=_percentile(s, 95), n=n)


def run_sensitivity(
    donor: str,
    demand: str,
    base: RunParams | None = None,
    monte_carlo: int = 0,
    seed: int = 0,
    catalog: dict[str, SectionProps] | None = None,
) -> SensitivityResult:
    """Full study: the tornado always, plus the Monte Carlo band when ``monte_carlo > 0``."""
    base = base or RunParams()
    catalog = catalog or load_default_catalog()
    baseline_co2, tornado = run_tornado(donor, demand, base, catalog=catalog)
    mc = run_monte_carlo(donor, demand, base, monte_carlo, seed, catalog) if monte_carlo > 0 else None
    return SensitivityResult(baseline=base, baseline_co2_kg=baseline_co2, tornado=tornado,
                             monte_carlo=mc)


# ---------------------------------------------------------------------------
# Rendering: CSV (stdlib) + a tornado chart (matplotlib, optional)
# ---------------------------------------------------------------------------

def tornado_to_csv(result: SensitivityResult) -> str:
    """CSV of the tornado: one row per driver with its low/high labels, CO2 values, and swing."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["driver", "baseline_co2_kg", "low_label", "low_co2_kg",
                "high_label", "high_co2_kg", "swing_kg"])
    for e in result.tornado:
        w.writerow([e.driver, round(result.baseline_co2_kg, 1),
                    e.low.label, round(e.low.co2_saved_kg, 1),
                    e.high.label, round(e.high.co2_saved_kg, 1), round(e.swing, 1)])
    return buf.getvalue()


def render_tornado_chart(result: SensitivityResult, path: str) -> bool:
    """Draw the tornado as a horizontal bar chart at ``path``. Returns False if matplotlib is absent."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    entries = list(reversed(result.tornado))  # largest swing on top
    labels = [e.driver for e in entries]
    base = result.baseline_co2_kg
    lows = [e.low.co2_saved_kg - base for e in entries]
    highs = [e.high.co2_saved_kg - base for e in entries]
    y = range(len(entries))

    fig, ax = plt.subplots(figsize=(8, 0.6 * len(entries) + 1.5))
    ax.barh(list(y), [h - lo for lo, h in zip(lows, highs, strict=True)],
            left=lows, color="#4C72B0", edgecolor="black")
    ax.axvline(0.0, color="black", linewidth=1)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.set_xlabel(f"CO2 saved relative to baseline = {base:.0f} kg (kg CO2e)")
    ax.set_title("Sensitivity of CO2 saved to design assumptions")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def format_summary(result: SensitivityResult) -> str:
    """A console-ready text summary of the tornado and (if present) the Monte Carlo band."""
    lines = [f"Baseline CO2 saved: {result.baseline_co2_kg:.1f} kg",
             "Tornado (driver: low -> high, swing):"]
    for e in result.tornado:
        lines.append(f"  {e.driver:<28} {e.low.co2_saved_kg:8.1f} -> {e.high.co2_saved_kg:8.1f}  "
                     f"(swing {e.swing:7.1f}; {e.low.label} / {e.high.label})")
    if result.monte_carlo:
        mc = result.monte_carlo
        lines.append(f"Monte Carlo (n={mc.n}): P5 {mc.p5:.1f} | P50 {mc.p50:.1f} | P95 {mc.p95:.1f} kg")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Console entry (``steelreuse-sensitivity``)."""
    import argparse
    from pathlib import Path

    from .resources import sample_path

    ap = argparse.ArgumentParser(
        prog="steelreuse-sensitivity",
        description="Sensitivity & uncertainty study of the headline CO2-saved figure: a tornado of "
                    "one-at-a-time driver swings plus an optional Monte Carlo confidence band.")
    ap.add_argument("--demo", action="store_true", help="run on the bundled sample models")
    ap.add_argument("--donor", help="donor model JSON")
    ap.add_argument("--demand", help="demand model JSON")
    ap.add_argument("--out", default="reports/sensitivity",
                    help="output directory for tornado.csv + tornado.png (default: reports/sensitivity)")
    ap.add_argument("--monte-carlo", type=int, default=0, metavar="N",
                    help="also run N Monte Carlo samples for a P5-P95 band (default: 0 = off)")
    ap.add_argument("--seed", type=int, default=0, help="Monte Carlo RNG seed (default: 0)")
    args = ap.parse_args(argv)

    if args.demo:
        donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
    elif args.donor and args.demand:
        donor, demand = args.donor, args.demand
    else:
        ap.error("provide --donor and --demand (or use --demo to run the bundled sample models)")

    result = run_sensitivity(donor, demand, monte_carlo=args.monte_carlo, seed=args.seed)
    print(format_summary(result))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tornado.csv").write_text(tornado_to_csv(result), encoding="utf-8")
    if render_tornado_chart(result, str(out / "tornado.png")):
        print(f"Wrote {out}/tornado.csv and tornado.png")
    else:
        print(f"Wrote {out}/tornado.csv (install the [analysis] extra for the tornado.png chart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
