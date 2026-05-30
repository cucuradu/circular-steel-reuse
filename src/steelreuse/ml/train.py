"""Generate the synthetic dataset, train the capacity surrogate, and save both as artifacts.

    uv run python -m steelreuse.ml.train

Writes ``data/generated/beam_dataset.csv`` and ``models/surrogate.json`` (+ ``.meta.json``).
The surrogate is a *speed-only pre-screen*; the deterministic EN 1993 check remains the source of truth.
"""

from __future__ import annotations

from pathlib import Path

from ..synthetic import generate_beam_dataset
from .surrogate import train_surrogate

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:  # pragma: no cover - exercised manually / as a script
    data_dir = ROOT / "data" / "generated"
    model_dir = ROOT / "models"
    data_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    print("generating synthetic dataset...")
    df = generate_beam_dataset()
    csv = data_dir / "beam_dataset.csv"
    df.to_csv(csv, index=False)
    print(f"  {len(df)} rows -> {csv}")

    print("training capacity surrogate...")
    model = train_surrogate(df)
    out = model_dir / "surrogate.json"
    model.save(out)
    print(f"  test R^2 = {model.r2:.4f} -> {out}")


if __name__ == "__main__":
    main()
