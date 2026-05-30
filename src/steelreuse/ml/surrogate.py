"""Capacity surrogate — an XGBoost regressor that predicts beam utilization fast.

Trained on the synthetic dataset (:mod:`steelreuse.synthetic`). Its only job is to *pre-screen*
large supply x demand grids cheaply before the exact EN 1993 check confirms survivors. It is never
the source of truth (see CLAUDE.md). We report test R^2 so its accuracy is explicit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.metrics import r2_score
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

from ..synthetic import FEATURES


@dataclass
class SurrogateModel:
    model: XGBRegressor
    r2: float           # on held-out test set
    features: list[str]

    def predict(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(self.model.predict(df[self.features]), index=df.index)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        self.model.save_model(str(path))
        path.with_suffix(".meta.json").write_text(
            json.dumps({"r2": self.r2, "features": self.features}), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> SurrogateModel:
        path = Path(path)
        model = XGBRegressor()
        model.load_model(str(path))
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        return cls(model=model, r2=meta["r2"], features=meta["features"])


def train_surrogate(df: pd.DataFrame, random_state: int = 0) -> SurrogateModel:
    X, y = df[FEATURES], df["utilization"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=random_state)
    model = XGBRegressor(
        n_estimators=300, max_depth=5, learning_rate=0.1, subsample=0.9, random_state=random_state,
    )
    model.fit(X_tr, y_tr)
    r2 = float(r2_score(y_te, model.predict(X_te)))
    return SurrogateModel(model=model, r2=r2, features=list(FEATURES))
