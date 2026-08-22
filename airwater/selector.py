from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

from airwater.physics import (
    COLLECTOR_AREA_M2_PER_KG,
    COLLECTOR_HEAT_LOSS_W_M2K,
    PRACTICAL_MAX_TEMP_C,
    REGEN_SAFETY_MARGIN_C,
    SOLAR_COLLECTION_EFFICIENCY,
    SORBENT_SPECIFIC_HEAT_KJ_PER_KGK,
    WATER_DESORPTION_ENTHALPY_KJ_PER_KG,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "candidate_mofs.csv"
ISOTHERMS_PATH = ROOT / "data" / "water_isotherms.csv"
MODEL_PATH = ROOT / "airwater" / "model_artifacts" / "water_uptake_rf.joblib"
METRICS_PATH = ROOT / "airwater" / "model_artifacts" / "metrics.json"

# Achievable regeneration temperature: steady-state flat-plate collector approximation,
# T_achievable = T_ambient + (solar_flux_w_m2 * collector_efficiency) / heat_loss_coeff.


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
    # Tier A/B candidates have rh50/steepness/max_uptake fit directly from a
    # real NIST ISODB water isotherm for this exact material -- interpolating
    # that measured curve is both more accurate and more auditable than
    # asking a tree ensemble trained on OTHER materials to guess it (leave-
    # one-MOF-out validation on this data showed the RF's cross-material R^2
    # is only ~0.24 vs ~0.84 for direct interpolation; see
    # airwater/model_artifacts/metrics.json). ML extrapolation is reserved
    # for Tier C materials that have no isotherm of their own on file.
    tier = str(row.get("tier", "")) if "tier" in row else ""
    if tier in ("A", "B"):
        return formula_uptake(row, rh_percent, temp_c), "measured isotherm interpolation (NIST ISODB)"
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
        return float(np.clip(pred, 0.0, row["max_uptake_kgkg"])), "ML extrapolation, RandomForestRegressor (no NIST isotherm on file)"
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


def _calibrated_uncertainty_fraction(row: pd.Series, metrics: Dict[str, Any]) -> Tuple[float, str]:
    """Relative half-width for the yield interval, derived from real residuals, not a fixed percentage.

    Tier A/B: this material's own held-out (or, for very small samples, in-sample)
    fit-residual quantiles from build_candidate_mofs.py, scaled by its max uptake.
    Tier C: the RandomForest's leave-one-MOF-out residual quantiles from
    train_demo_model.py (metrics.json), scaled by the training data's mean uptake --
    the best available estimate of how wrong ML extrapolation tends to be for a
    material with no isotherm of its own.
    """
    tier = str(row.get("tier", ""))
    max_uptake = max(float(row.get("max_uptake_kgkg", 0.0)), 1e-6)
    if tier in ("A", "B") and pd.notna(row.get("resid_p05_kgkg")) and pd.notna(row.get("resid_p95_kgkg")):
        p05, p95 = float(row["resid_p05_kgkg"]), float(row["resid_p95_kgkg"])
        method = str(row.get("interval_method", "calibrated"))
        return (p95 - p05) / (2.0 * max_uptake), method
    interval = metrics.get("prediction_interval", {})
    lo, hi = interval.get("lower_offset_kgkg"), interval.get("upper_offset_kgkg")
    if lo is not None and hi is not None:
        return (float(hi) - float(lo)) / (2.0 * max_uptake), "rf_leave_one_mof_out_residuals"
    return 0.35, "fallback_uncalibrated"


def _confidence_range(liters: float, uncertainty_fraction: float) -> Tuple[float, float]:
    fraction = float(np.clip(uncertainty_fraction, 0.05, 0.90))
    return liters * (1.0 - fraction), liters * (1.0 + fraction)


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
    metrics = load_metrics()
    adsorb_hours, desorb_hours, scored_climate = select_windows(climate, energy_source)

    ads_df = climate[climate["hour"].isin(adsorb_hours)]
    des_df = climate[climate["hour"].isin(desorb_hours)]
    adsorption_rh = float(ads_df["relative_humidity_percent"].mean())
    adsorption_temp = float(ads_df["temperature_c"].mean())

    # Achievable regen temperature actually available in this scenario, not just
    # the user's stated heat-source limit -- see COLLECTOR_HEAT_LOSS_W_M2K above.
    ambient_desorb_temp = float(des_df["temperature_c"].mean()) if len(des_df) else float(climate["temperature_c"].mean())
    if energy_source == "Solar only":
        avg_solar_desorb_w_m2 = float(des_df["solar_w_m2"].mean()) if len(des_df) else 0.0
        achievable_temp_c = ambient_desorb_temp + (avg_solar_desorb_w_m2 * SOLAR_COLLECTION_EFFICIENCY) / COLLECTOR_HEAT_LOSS_W_M2K
    else:
        achievable_temp_c = PRACTICAL_MAX_TEMP_C.get(energy_source, 150.0)
    regen_ceiling_c = min(max_regen_temp_c, achievable_temp_c) - REGEN_SAFETY_MARGIN_C
    residual_rh = max(5.0, float(des_df["relative_humidity_percent"].quantile(0.20)) * 0.35)

    # Real energy available to run regeneration cycles, not just window time --
    # mirrors decision.py's estimate_energy_and_cost() so the two stay consistent.
    available_energy_kwh_day = None
    if energy_source == "Solar only":
        solar_wh_desorb = float(des_df["solar_w_m2"].sum())
        available_energy_kwh_day = solar_wh_desorb * COLLECTOR_AREA_M2_PER_KG * mass_kg * SOLAR_COLLECTION_EFFICIENCY / 1000.0

    outputs: List[Dict[str, Any]] = []
    for _, row in mofs.iterrows():
        # A material only needs to reach its OWN regen_temp_c, not the full
        # achievable ceiling -- heating further wastes energy for no benefit.
        material_desorb_temp = min(achievable_temp_c, max_regen_temp_c, float(row["regen_temp_c"]))
        uptake_ads, model_source = predict_uptake(row, adsorption_rh, adsorption_temp, model)
        residual, _ = predict_uptake(row, residual_rh, material_desorb_temp, model)
        working_capacity = max(uptake_ads - residual, 0.0)

        # Trequired <= Tachievable - safety margin (the actual feasibility gate).
        regen_gap = max(float(row["regen_temp_c"]) - regen_ceiling_c, 0.0)
        regen_margin = regen_ceiling_c - float(row["regen_temp_c"])
        regen_penalty = float(np.clip(regen_gap / 35.0, 0, 1))
        feasible = regen_gap <= 0

        # Cycles/day = how many full adsorb+desorb cycles actually fit in the
        # day's capture and release windows, not a fudge-factor guess. Each
        # cycle is assumed to split its time evenly between an adsorption
        # dwell and a desorption+cool dwell (documented simplification --
        # cycle_minutes isn't broken into phases by any source data).
        cycle_hours = max(float(row["cycle_minutes"]) / 60.0, 0.1)
        half_cycle_hours = cycle_hours / 2.0
        max_cycles_by_duration = int(np.floor(24.0 / cycle_hours))
        max_cycles_by_adsorb_window = int(np.floor(len(adsorb_hours) / half_cycle_hours))
        max_cycles_by_desorb_window = int(np.floor(len(desorb_hours) / half_cycle_hours))
        cycle_limit = max(1, min(max_cycles_by_duration, max_cycles_by_adsorb_window, max_cycles_by_desorb_window))
        if not feasible:
            cycle_limit = max(1, cycle_limit // 2)

        # Also cap by the energy actually available -- a cycle count that fits
        # the time windows can still be more regeneration energy than a solar
        # collector this size can supply in a day (see estimate_energy_and_cost
        # in decision.py, which uses this same formula for the winning candidate).
        delta_t = max(material_desorb_temp - ambient_desorb_temp, 5.0)
        thermal_kj_per_cycle = (
            mass_kg * SORBENT_SPECIFIC_HEAT_KJ_PER_KGK * delta_t
            + mass_kg * working_capacity * WATER_DESORPTION_ENTHALPY_KJ_PER_KG
        )
        energy_per_cycle_kwh = thermal_kj_per_cycle / 3600.0
        if available_energy_kwh_day is not None and energy_per_cycle_kwh > 0:
            max_cycles_by_energy = int(np.floor(available_energy_kwh_day / energy_per_cycle_kwh))
            cycle_limit = max(1, min(cycle_limit, max_cycles_by_energy)) if max_cycles_by_energy > 0 else 1

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
        # Three separate concepts, not one blended "confidence" score:
        # evidence quality (how much real isotherm support exists), prediction
        # uncertainty (calibrated interval width from real residuals), and
        # deployment readiness (the separate pass/fail refusal gate in decision.py).
        if evidence >= 0.75:
            evidence_quality = "Strong"
        elif evidence >= 0.50:
            evidence_quality = "Moderate"
        else:
            evidence_quality = "Limited"

        uncertainty_fraction, uncertainty_method = _calibrated_uncertainty_fraction(row, metrics)
        prediction_uncertainty_percent = round(uncertainty_fraction * 100, 1)
        if uncertainty_fraction < 0.20:
            prediction_uncertainty_label = "Low"
        elif uncertainty_fraction < 0.40:
            prediction_uncertainty_label = "Moderate"
        else:
            prediction_uncertainty_label = "High"
        # kept for back-compat call sites; now inverse of the calibrated uncertainty width
        # (Low uncertainty -> High confidence), not an arbitrary weighted score.
        confidence = {"Low": "High", "Moderate": "Moderate", "High": "Low"}[prediction_uncertainty_label]

        low, high = _confidence_range(liters, uncertainty_fraction)
        if regen_gap > 0:
            limitation = f"Regeneration target is {regen_gap:.0f} C above the achievable bed temperature (heat source and/or user heat limit) in this scenario."
        elif working_capacity < 0.05:
            limitation = "Low predicted working capacity in this humidity window."
        elif evidence < 0.7:
            limitation = "Evidence base is thinner than the top candidates."
        else:
            limitation = "Requires device-scale, cycling, leaching, and water-quality validation."

        ci_width_relative = (high - low) / max(liters, 1e-6)

        outputs.append(
            {
                "name": row["name"],
                "short_name": row["short_name"],
                "metal_family": row["metal_family"],
                "score": round(float(score), 3),
                "target_score": round(float(target_score), 3),
                "regen_penalty": round(float(regen_penalty), 3),
                "feasible": bool(feasible),
                "ci_width_relative": round(float(ci_width_relative), 3),
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
                "desorption_temp_c": round(material_desorb_temp, 1),
                "residual_rh_percent": round(residual_rh, 1),
                "data_rh_min_percent": float(row["data_rh_min_percent"]) if "data_rh_min_percent" in row and pd.notna(row["data_rh_min_percent"]) else None,
                "data_rh_max_percent": float(row["data_rh_max_percent"]) if "data_rh_max_percent" in row and pd.notna(row["data_rh_max_percent"]) else None,
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
                "evidence_quality": evidence_quality,
                "prediction_uncertainty_percent": prediction_uncertainty_percent,
                "prediction_uncertainty_label": prediction_uncertainty_label,
                "uncertainty_method": uncertainty_method,
                "achievable_regen_temp_c": round(achievable_temp_c, 1),
                "regen_ceiling_c": round(regen_ceiling_c, 1),
                "limitation": limitation,
                "model_source": model_source,
                "notes": row["notes"],
                "source_hint": row["source_hint"],
                "tier": row.get("tier", ""),
                "evidence_source": row.get("evidence_source", ""),
                "n_isotherms": int(row["n_isotherms"]) if "n_isotherms" in row and pd.notna(row["n_isotherms"]) else 0,
                "n_papers": int(row["n_papers"]) if "n_papers" in row and pd.notna(row["n_papers"]) else 0,
                "doi_list": row.get("doi_list", "") or "",
                "data_quality_flags": row.get("data_quality_flags", "") or "",
                "fit_r2": float(row["fit_r2"]) if "fit_r2" in row and pd.notna(row["fit_r2"]) else None,
                "evidence_components": {
                    "has_water_isotherm": int(row["evidence_has_isotherm"]),
                    "independent_sources": int(row["evidence_independent_sources"]),
                    "temperature_coverage": int(row["evidence_temperature_coverage"]),
                    "point_density": int(row["evidence_point_density"]),
                    "regeneration_or_stability_data": int(row["evidence_regen_stability_data"]),
                    "data_quality_penalty": int(row["evidence_quality_penalty"]),
                } if "evidence_has_isotherm" in row and pd.notna(row["evidence_has_isotherm"]) else None,
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


@lru_cache(maxsize=1)
def _load_isotherm_points() -> pd.DataFrame:
    if not ISOTHERMS_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(ISOTHERMS_PATH)


def get_isotherm_detail(material_name: str) -> Dict[str, Any]:
    """Real digitized measurement points + fit + validation context for one material.

    Powers the evidence view: what was actually measured, what the model fit
    to it, and (for materials with no isotherm at all) the nearest evidence
    we do have -- the RF's leave-one-MOF-out validation on the real materials.
    """
    mofs = load_mofs()
    match = mofs[mofs["name"] == material_name]
    if match.empty:
        raise ValueError(f"Unknown material: {material_name}")
    row = match.iloc[0]

    points_df = _load_isotherm_points()
    if not points_df.empty:
        material_points = points_df[points_df["material_name"] == material_name]
    else:
        material_points = points_df
    measured_points = [
        {
            "rh_percent": float(p["relative_humidity_percent"]),
            "uptake_kgkg": float(p["uptake_kg_per_kg"]),
            "temperature_k": float(p["temperature_K"]),
            "isotherm_id": str(p["NIST_isotherm_id"]),
            "doi": str(p["DOI"]),
            "branch": str(p["adsorption_or_desorption_branch"]),
        }
        for _, p in material_points.iterrows()
        if pd.notna(p["relative_humidity_percent"]) and pd.notna(p["uptake_kg_per_kg"])
    ] if not material_points.empty else []

    metrics = load_metrics()
    return {
        "material": material_name,
        "short_name": str(row.get("short_name", material_name)),
        "tier": str(row.get("tier", "C")),
        "measured_points": measured_points,
        "fit": {
            "max_uptake_kgkg": float(row["max_uptake_kgkg"]),
            "rh50_percent": float(row["rh50_percent"]),
            "steepness": float(row["steepness"]),
            "r2": float(row["fit_r2"]) if pd.notna(row.get("fit_r2")) else None,
        },
        "data_rh_range": (
            [float(row["data_rh_min_percent"]), float(row["data_rh_max_percent"])]
            if pd.notna(row.get("data_rh_min_percent")) else None
        ),
        "n_isotherms": int(row["n_isotherms"]) if pd.notna(row.get("n_isotherms")) else 0,
        "n_papers": int(row["n_papers"]) if pd.notna(row.get("n_papers")) else 0,
        "doi_list": [d for d in str(row["doi_list"] if pd.notna(row.get("doi_list")) else "").split(";") if d],
        "data_quality_flags": [f for f in str(row["data_quality_flags"] if pd.notna(row.get("data_quality_flags")) else "").split(";") if f],
        "interval_method": row.get("interval_method") if pd.notna(row.get("interval_method")) else None,
        "resid_p05_kgkg": float(row["resid_p05_kgkg"]) if pd.notna(row.get("resid_p05_kgkg")) else None,
        "resid_p95_kgkg": float(row["resid_p95_kgkg"]) if pd.notna(row.get("resid_p95_kgkg")) else None,
        "lomo_validation": {
            "per_material_mae_kgkg": metrics.get("per_material_mae_kgkg", {}),
            "overall_mae_kgkg": metrics.get("mae_kgkg"),
            "overall_r2": metrics.get("r2"),
            "prediction_interval": metrics.get("prediction_interval", {}),
        },
    }


def list_materials() -> pd.DataFrame:
    """Full candidate library, independent of any climate scenario -- for browsing, not ranking."""
    tier_order = {"A": 0, "B": 1, "C": 2}
    mofs = load_mofs().copy()
    mofs["_tier_order"] = mofs["tier"].map(tier_order).fillna(9)
    return mofs.sort_values(["_tier_order", "name"]).drop(columns="_tier_order").reset_index(drop=True)


def load_feature_importance() -> pd.DataFrame:
    metrics = load_metrics()
    model = _load_model()
    features = list(metrics.get("features", []))
    if model is None or not features or not hasattr(model, "feature_importances_"):
        return pd.DataFrame(columns=["feature", "importance"])
    values = np.asarray(model.feature_importances_, dtype=float)
    count = min(len(features), len(values))
    return pd.DataFrame({"feature": features[:count], "importance": values[:count]})
