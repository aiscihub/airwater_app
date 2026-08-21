from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "candidate_mofs.csv"
OUT = ROOT / "airwater" / "model_artifacts"
OUT.mkdir(exist_ok=True)


def formula(row: pd.Series, rh_percent: float, temp_c: float) -> float:
    max_uptake = float(row["max_uptake_kgkg"])
    rh50 = float(row["rh50_percent"])
    steepness = float(row["steepness"])
    base = max_uptake / (1.0 + np.exp(-steepness * (rh_percent - rh50)))
    temp_penalty = np.clip(1.0 - 0.004 * max(temp_c - 25.0, 0.0), 0.70, 1.05)
    return float(np.clip(base * temp_penalty, 0.0, max_uptake))


def build_training_rows(mofs: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for _, row in mofs.iterrows():
        for rh in np.linspace(5, 95, 31):
            for temp in [15, 25, 35, 45, 60]:
                y = formula(row, rh, temp)
                y += rng.normal(0, 0.012)
                y = float(np.clip(y, 0.0, row["max_uptake_kgkg"]))
                rows.append(
                    {
                        "mof": row["name"],
                        "relative_humidity_percent": rh,
                        "temperature_c": temp,
                        "max_uptake_kgkg": row["max_uptake_kgkg"],
                        "rh50_percent": row["rh50_percent"],
                        "steepness": row["steepness"],
                        "regen_temp_c": row["regen_temp_c"],
                        "cycle_minutes": row["cycle_minutes"],
                        "water_stability_score": row["water_stability_score"],
                        "cost_score": row["cost_score"],
                        "pore_volume_cm3g": row["pore_volume_cm3g"],
                        "surface_area_m2g": row["surface_area_m2g"],
                        "uptake_kgkg": y,
                    }
                )
    return pd.DataFrame(rows)


def main() -> None:
    mofs = pd.read_csv(DATA)
    df = build_training_rows(mofs)
    feature_cols = [
        "relative_humidity_percent",
        "temperature_c",
        "max_uptake_kgkg",
        "rh50_percent",
        "steepness",
        "regen_temp_c",
        "cycle_minutes",
        "water_stability_score",
        "cost_score",
        "pore_volume_cm3g",
        "surface_area_m2g",
    ]
    groups = df["mof"]
    splitter = GroupShuffleSplit(n_splits=1, train_size=0.75, random_state=11)
    train_idx, test_idx = next(splitter.split(df, df["uptake_kgkg"], groups))
    train = df.iloc[train_idx]
    test = df.iloc[test_idx]

    model = RandomForestRegressor(
        n_estimators=240,
        max_depth=9,
        random_state=42,
        min_samples_leaf=3,
    )
    model.fit(train[feature_cols], train["uptake_kgkg"])
    pred = model.predict(test[feature_cols])
    rmse = float(mean_squared_error(test["uptake_kgkg"], pred) ** 0.5)
    mae = float(mean_absolute_error(test["uptake_kgkg"], pred))
    r2 = float(r2_score(test["uptake_kgkg"], pred))

    metrics = {
        "model": "RandomForestRegressor demo model",
        "training_rows": int(len(train)),
        "test_rows": int(len(test)),
        "split": "GroupShuffleSplit by MOF name",
        "mae_kgkg": round(mae, 4),
        "rmse_kgkg": round(rmse, 4),
        "r2": round(r2, 3),
        "important_note": "Synthetic demo target generated from descriptor curves. Replace with curated experimental isotherm data for final science claims.",
        "features": feature_cols,
        "held_out_mofs": sorted(test["mof"].unique().tolist()),
    }
    joblib.dump(model, OUT / "water_uptake_rf.joblib")
    with open(OUT / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    df.to_csv(OUT / "synthetic_training_data.csv", index=False)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
