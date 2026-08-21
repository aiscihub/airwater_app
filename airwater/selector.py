from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "candidate_mofs.csv"
MODEL_PATH = ROOT / "airwater" / "model_artifacts" / "water_uptake_rf.joblib"
METRICS_PATH = ROOT / "airwater" / "model_artifacts" / "metrics.json"


@lru_cache(maxsize=1)
def load_mofs(path: Path = DATA_PATH) -> pd.DataFrame:
    return pd.read_csv(path)


def formula_uptake(row: pd.Series, rh_percent: float, temp_c: float) -> float:
    """Transparent demonstration uptake approximation in kg water/kg MOF."""
    max_uptake = float(row["max_uptake_kgkg"])
    rh50 = float(row["rh50_percent"])
    steepness = float(row["steepness"])
    base = max_uptake / (1.0 + np.exp(-steepness * (rh_percent - rh50)))
    temp_penalty = np.clip(1.0 - 0.004 * max(temp_c - 25.0, 0.0), 0.70, 1.05)
    return float(np.clip(base * temp_penalty, 0.0, max_uptake))


@lru_cache(maxsize=1)
def _load_model() -> Any | None:
    if MODEL_PATH.exists():
        try:
            return joblib.load(MODEL_PATH)
        except Exception:
            return None
    return None


def predict_uptake(
    row: pd.Series,
    rh_percent: float,
    temp_c: float,
    model: Any | None = None,
) -> Tuple[float, str]:
    if model is None:
        return formula_uptake(row, rh_percent, temp_c), "formula fallback"
    X = pd.DataFrame(
        [
            {
                "relative_humidity_percent": rh_percent,
                "temperature_c": temp_c,
                "max_uptake_kgkg": row["max_uptake_kgkg"],
                "rh50_percent": row["rh50_percent"],
                "steepness": row["steepness"],
                "regen_temp_c": row["regen_temp_c"],
                "cycle_minutes": row["cycle_minutes"],
                "water_stability_score": row["water_stability_score"],
                "cost_score": row["cost_score"],
                "pore_volume_cm3g": row["pore_volume_cm3g"],
                "surface_area_m2g": row["surface_area_m2g"],
            }
        ]
    )
    try:
        pred = float(model.predict(X)[0])
        return float(np.clip(pred, 0.0, row["max_uptake_kgkg"])), "random-forest demo model"
    except Exception:
        return formula_uptake(row, rh_percent, temp_c), "formula fallback"


def _best_contiguous_window(
    df: pd.DataFrame,
    score_col: str,
    length: int,
    excluded: set[int] | None = None,
) -> List[int]:
    excluded = excluded or set()
    scores = df.set_index("hour")[score_col].to_dict()
    best_hours: List[int] = []
    best_score = float("-inf")
    for start in range(24):
        hours = [(start + offset) % 24 for offset in range(length)]
        overlap = len(set(hours).intersection(excluded))
        score = float(np.mean([scores.get(hour, 0.0) for hour in hours])) - overlap * 0.35
        if score > best_score:
            best_score = score
            best_hours = hours
    return sorted(best_hours)


def select_windows(climate: pd.DataFrame, energy_source: str) -> Tuple[List[int], List[int], pd.DataFrame]:
    df = climate.copy()
    df["adsorb_score"] = (
        0.60 * df["relative_humidity_percent"].rank(pct=True)
        + 0.25 * (-df["temperature_c"]).rank(pct=True)
        + 0.15 * (df["solar_w_m2"] < 80).astype(float)
    )
    if energy_source == "Solar only":
        df["desorb_score"] = (
            0.78 * df["solar_w_m2"].rank(pct=True)
            + 0.22 * df["temperature_c"].rank(pct=True)
        )
    elif energy_source == "Waste heat":
        df["desorb_score"] = 0.60 * df["temperature_c"].rank(pct=True) + 0.40
    else:
        df["desorb_score"] = 0.35 * df["solar_w_m2"].rank(pct=True) + 0.65 * df["temperature_c"].rank(pct=True)

    adsorb_hours = _best_contiguous_window(df, "adsorb_score", length=8)
    desorb_hours = _best_contiguous_window(df, "desorb_score", length=5, excluded=set(adsorb_hours))
    return adsorb_hours, desorb_hours, df


def _confidence_range(liters: float, confidence: str) -> Tuple[float, float]:
    if confidence == "High":
        return liters * 0.82, liters * 1.18
    if confidence == "Moderate":
        return liters * 0.72, liters * 1.30
    return liters * 0.62, liters * 1.42


def rank_mofs(
    climate: pd.DataFrame,
    mass_kg: float,
    max_regen_temp_c: float,
    target_liters_day: float,
    energy_source: str,
    efficiency: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    mofs = load_mofs()
    model = _load_model()
    adsorb_hours, desorb_hours, scored_climate = select_windows(climate, energy_source)

    ads_df = climate[climate["hour"].isin(adsorb_hours)]
    des_df = climate[climate["hour"].isin(desorb_hours)]
    adsorption_rh = float(ads_df["relative_humidity_percent"].mean())
    adsorption_temp = float(ads_df["temperature_c"].mean())
    desorption_temp = min(max_regen_temp_c, max(float(des_df["temperature_c"].mean()) + 45.0, 55.0))
    residual_rh = max(5.0, float(des_df["relative_humidity_percent"].quantile(0.20)) * 0.35)

    outputs: List[Dict[str, Any]] = []
    for _, row in mofs.iterrows():
        uptake_ads, model_source = predict_uptake(row, adsorption_rh, adsorption_temp, model)
        residual, _ = predict_uptake(row, residual_rh, desorption_temp, model)
        working_capacity = max(uptake_ads - residual, 0.0)

        regen_gap = max(float(row["regen_temp_c"]) - max_regen_temp_c, 0.0)
        regen_margin = max_regen_temp_c - float(row["regen_temp_c"])
        regen_penalty = float(np.clip(regen_gap / 35.0, 0, 1))
        feasible = regen_gap <= 12
        cycle_hours = float(row["cycle_minutes"]) / 60.0
        cycle_limit = int(max(1, np.floor((len(adsorb_hours) + len(desorb_hours)) / max(cycle_hours, 0.25) * 0.20)))
        if energy_source == "Solar only":
            cycle_limit = min(cycle_limit, max(1, int(np.sum(climate["solar_w_m2"] > 250) // 2)))
        elif energy_source == "Electricity or hybrid":
            cycle_limit = min(cycle_limit + 2, 12)
        else:
            cycle_limit = min(cycle_limit + 1, 10)
        if not feasible:
            cycle_limit = max(1, cycle_limit - 1)

        liters = float(mass_kg * working_capacity * cycle_limit * efficiency)
        target_fraction = liters / max(target_liters_day, 0.01)
        target_score = min(target_fraction, 1.5)
        evidence = float(row["evidence_score"])
        stability = float(row["water_stability_score"])
        cost = float(row["cost_score"])
        climate_fit = float(np.clip(1.0 - abs(adsorption_rh - float(row["rh50_percent"])) / 65.0, 0.0, 1.0))
        score = (
            0.43 * target_score
            + 0.19 * evidence
            + 0.13 * stability
            + 0.09 * cost
            + 0.16 * climate_fit
            - 0.20 * regen_penalty
        )
        confidence_raw = 0.42 * evidence + 0.28 * stability + 0.18 * climate_fit + 0.12 * (1 - regen_penalty)
        if confidence_raw >= 0.78:
            confidence = "High"
        elif confidence_raw >= 0.60:
            confidence = "Moderate"
        else:
            confidence = "Low"

        low, high = _confidence_range(liters, confidence)
        if regen_gap > 0:
            limitation = f"Regeneration target is {regen_gap:.0f} C above the selected heat limit."
        elif working_capacity < 0.05:
            limitation = "Low predicted working capacity in this humidity window."
        elif evidence < 0.7:
            limitation = "Evidence base is thinner than the top candidates."
        else:
            limitation = "Requires device-scale, cycling, leaching, and water-quality validation."

        outputs.append(
            {
                "name": row["name"],
                "short_name": row["short_name"],
                "metal_family": row["metal_family"],
                "score": round(float(score), 3),
                "predicted_working_capacity_kgkg": round(float(working_capacity), 3),
                "uptake_at_capture_kgkg": round(float(uptake_ads), 3),
                "residual_uptake_kgkg": round(float(residual), 3),
                "estimated_liters_day": round(liters, 2),
                "yield_low_liters_day": round(low, 2),
                "yield_high_liters_day": round(high, 2),
                "estimated_range": f"{low:.2f}-{high:.2f} L/day",
                "target_coverage_percent": round(target_fraction * 100, 1),
                "meets_target": bool(liters >= target_liters_day),
                "cycles_day": cycle_limit,
                "adsorption_rh_percent": round(adsorption_rh, 1),
                "adsorption_temp_c": round(adsorption_temp, 1),
                "desorption_temp_c": round(desorption_temp, 1),
                "regen_temp_c": float(row["regen_temp_c"]),
                "regen_margin_c": round(regen_margin, 1),
                "rh50_percent": float(row["rh50_percent"]),
                "max_uptake_kgkg": float(row["max_uptake_kgkg"]),
                "cycle_minutes": int(row["cycle_minutes"]),
                "climate_fit_score": round(climate_fit, 3),
                "water_stability_score": float(row["water_stability_score"]),
                "cost_score": float(row["cost_score"]),
                "evidence_score": evidence,
                "confidence": confidence,
                "limitation": limitation,
                "model_source": model_source,
                "notes": row["notes"],
                "source_hint": row["source_hint"],
            }
        )

    result = pd.DataFrame(outputs).sort_values("score", ascending=False).reset_index(drop=True)
    schedule_rows = []
    for _, row in scored_climate.iterrows():
        hour = int(row["hour"])
        if hour in adsorb_hours:
            action = "Capture"
        elif hour in desorb_hours:
            action = "Release + condense"
        else:
            action = "Idle / monitor"
        schedule_rows.append(
            {
                "hour": hour,
                "action": action,
                "temperature_c": float(row["temperature_c"]),
                "relative_humidity_percent": float(row["relative_humidity_percent"]),
                "solar_w_m2": float(row["solar_w_m2"]),
                "capture_score": round(float(row["adsorb_score"]), 3),
                "release_score": round(float(row["desorb_score"]), 3),
            }
        )
    return result, pd.DataFrame(schedule_rows)


@lru_cache(maxsize=1)
def load_metrics() -> Dict[str, Any]:
    if METRICS_PATH.exists():
        with open(METRICS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "model": "Formula fallback",
        "mae_kgkg": None,
        "rmse_kgkg": None,
        "r2": None,
        "note": "Run scripts/train_demo_model.py to create demo metrics.",
    }


def load_feature_importance() -> pd.DataFrame:
    metrics = load_metrics()
    model = _load_model()
    features = list(metrics.get("features", []))
    if model is None or not features or not hasattr(model, "feature_importances_"):
        return pd.DataFrame(columns=["feature", "importance"])
    values = np.asarray(model.feature_importances_, dtype=float)
    count = min(len(features), len(values))
    return pd.DataFrame({"feature": features[:count], "importance": values[:count]})
