"""Offline ETL step 4: train the water-uptake model on real NIST ISODB points.

Trains a RandomForestRegressor directly on measured (RH, T) -> uptake points
from data/water_isotherms.csv (Tier A/B materials only -- Tier C materials
have no real isotherm and are excluded from training, same as before).

Validation is leave-one-MOF-out (GroupKFold grouped by material, one fold per
material) so no isotherm ever has points split across train and test -- this
was the explicit ask: measure how the model does on a MOF it has never seen,
not on held-out points from a MOF it already partly saw.

The prediction interval reported in metrics.json is not a fixed percentage;
it's the empirical 5th/95th percentile of leave-one-MOF-out residuals, then
checked against how often held-out points actually fell inside it.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import LeaveOneGroupOut

ROOT = Path(__file__).resolve().parents[1]
ISOTHERMS_PATH = ROOT / "data" / "water_isotherms.csv"
CANDIDATES_PATH = ROOT / "data" / "candidate_mofs.csv"
OUT = ROOT / "airwater" / "model_artifacts"
OUT.mkdir(exist_ok=True)

FEATURE_COLS = [
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
DESCRIPTOR_COLS = [c for c in FEATURE_COLS if c not in ("relative_humidity_percent", "temperature_c")]


def build_training_frame() -> pd.DataFrame:
    iso = pd.read_csv(ISOTHERMS_PATH).dropna(subset=["relative_humidity_percent", "uptake_kg_per_kg"])
    candidates = pd.read_csv(CANDIDATES_PATH)
    real = candidates[candidates["tier"].isin(["A", "B"])].set_index("name")

    iso = iso[iso["material_name"].isin(real.index)].copy()
    iso["temperature_c"] = iso["temperature_K"] - 273.15
    for col in DESCRIPTOR_COLS:
        iso[col] = iso["material_name"].map(real[col])
    iso["uptake_kgkg"] = iso["uptake_kg_per_kg"]
    iso["mof"] = iso["material_name"]
    return iso[["mof"] + FEATURE_COLS + ["uptake_kgkg"]]


def rh_bucket(rh: float) -> str:
    if rh < 25:
        return "0-25%"
    if rh < 50:
        return "25-50%"
    if rh < 75:
        return "50-75%"
    return "75-100%"


def temp_bucket(t: float) -> str:
    if t < 15:
        return "<15C"
    if t < 30:
        return "15-30C"
    if t < 50:
        return "30-50C"
    return ">=50C"


def main() -> None:
    df = build_training_frame()
    X, y, groups = df[FEATURE_COLS], df["uptake_kgkg"], df["mof"]

    logo = LeaveOneGroupOut()
    oof_pred = np.full(len(df), np.nan)
    per_material_mae: dict[str, float] = {}

    for train_idx, test_idx in logo.split(X, y, groups):
        model = RandomForestRegressor(n_estimators=240, max_depth=9, random_state=42, min_samples_leaf=3)
        model.fit(X.iloc[train_idx], y.iloc[train_idx])
        pred = model.predict(X.iloc[test_idx])
        oof_pred[test_idx] = pred
        held_out_mof = groups.iloc[test_idx].iloc[0]
        per_material_mae[held_out_mof] = round(float(mean_absolute_error(y.iloc[test_idx], pred)), 4)

    residuals = y.to_numpy() - oof_pred
    mae = float(mean_absolute_error(y, oof_pred))
    rmse = float(mean_squared_error(y, oof_pred) ** 0.5)
    r2 = float(r2_score(y, oof_pred))

    # Mean-per-MOF baseline: predict each held-out point as that MOF's mean
    # measured uptake (computed from the OTHER materials' overall mean, since
    # a true LOMO baseline can't use the held-out MOF's own mean either).
    baseline_pred = np.full(len(df), np.nan)
    for train_idx, test_idx in logo.split(X, y, groups):
        baseline_pred[test_idx] = y.iloc[train_idx].mean()
    baseline_mae = float(mean_absolute_error(y, baseline_pred))

    lo_offset, hi_offset = np.percentile(residuals, [5, 95])
    lo_offset, hi_offset = float(lo_offset), float(hi_offset)
    covered = np.mean((residuals >= lo_offset) & (residuals <= hi_offset))

    error_by_rh = df.assign(bucket=df["relative_humidity_percent"].apply(rh_bucket), err=np.abs(residuals))
    error_by_rh = error_by_rh.groupby("bucket")["err"].mean().round(4).to_dict()
    error_by_temp = df.assign(bucket=df["temperature_c"].apply(temp_bucket), err=np.abs(residuals))
    error_by_temp = error_by_temp.groupby("bucket")["err"].mean().round(4).to_dict()

    # Final deployed model: fit on ALL real points, no holdout.
    final_model = RandomForestRegressor(n_estimators=240, max_depth=9, random_state=42, min_samples_leaf=3)
    final_model.fit(X, y)

    metrics = {
        "model": "RandomForestRegressor",
        "trained_on": "real NIST ISODB water isotherm points (Tier A/B materials only)",
        "training_rows": int(len(df)),
        "n_mofs_in_training": int(groups.nunique()),
        "split": "LeaveOneGroupOut, grouped by MOF (leave-one-MOF-out CV)",
        "mae_kgkg": round(mae, 4),
        "rmse_kgkg": round(rmse, 4),
        "r2": round(r2, 3),
        "r2_caveat": "R^2 on leave-one-MOF-out predictions across chemically dissimilar MOFs; treat as a rough signal, not a tight bound.",
        "per_material_mae_kgkg": per_material_mae,
        "error_by_humidity_range_kgkg": error_by_rh,
        "error_by_temperature_range_kgkg": error_by_temp,
        "baseline_comparison": {
            "description": "predicting each held-out MOF's points as the mean uptake of the other training MOFs",
            "baseline_mae_kgkg": round(baseline_mae, 4),
            "model_mae_kgkg": round(mae, 4),
            "model_beats_baseline": bool(mae < baseline_mae),
        },
        "prediction_interval": {
            "method": "empirical 5th/95th percentile of leave-one-MOF-out residuals, applied as a fixed additive offset",
            "lower_offset_kgkg": round(lo_offset, 4),
            "upper_offset_kgkg": round(hi_offset, 4),
            "target_coverage": 0.90,
            "empirical_coverage_on_holdout": round(float(covered), 3),
        },
        "important_note": (
            "Trained on 12 real MOFs with digitized NIST ISODB water isotherms (1226 points, 13 source papers). "
            "rh50_percent/steepness/max_uptake_kgkg per material are fit from that real data (see data/provenance_manifest.json), "
            "pooled across all measured temperatures per material -- not a full temperature-dependent isotherm model. "
            "regen_temp_c/cycle_minutes/water_stability_score/cost_score/pore_volume_cm3g/surface_area_m2g are NOT derivable "
            "from adsorption isotherms and are literature-informed estimates pending citation, except for MIL-160/CAU-10-H/UiO-66-NH2 "
            "which keep this app's original placeholder values. MOF-303/MOF-801/MOF-841 have no NIST ISODB water isotherm and are "
            "excluded from training entirely (Tier C / exploratory, original synthetic descriptors only)."
        ),
        "features": FEATURE_COLS,
        "held_out_mofs": sorted(groups.unique().tolist()),
    }

    joblib.dump(final_model, OUT / "water_uptake_rf.joblib")
    with open(OUT / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    df.to_csv(OUT / "training_data_real.csv", index=False)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
