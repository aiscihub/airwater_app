from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from airwater.climate import synthetic_profile

# Physical/economic assumptions (documented; not experimentally calibrated).
SORBENT_SPECIFIC_HEAT_KJ_PER_KGK = 1.2  # typical porous solid/composite heat capacity
WATER_DESORPTION_ENTHALPY_KJ_PER_KG = 2450.0  # vaporization enthalpy plus MOF binding-energy premium
COLLECTOR_AREA_M2_PER_KG = 0.35  # assumed solar-thermal collector footprint scaling with sorbent mass
SOLAR_COLLECTION_EFFICIENCY = 0.45  # flat-plate/PV-thermal hybrid collector efficiency
ENERGY_COST_PER_KWH = {"Solar only": 0.03, "Waste heat": 0.01, "Electricity or hybrid": 0.15}
MATERIAL_COST_FACTOR_PER_KG_CYCLE = 0.08  # amortized sorbent wear/replacement cost at cost_score=0

CI_WIDTH_RELATIVE_THRESHOLD = 0.40  # spec's documented tau ("e.g., 30% relative"), widened for demo bands
OOD_Z_THRESHOLD = 1.8  # normalized-centroid-distance heuristic in place of Mahalanobis-in-PCA-space


def _archetype_centroids() -> Dict[int, Dict[str, Any]]:
    kinds = [(0, "hot_dry"), (1, "warm_humid"), (2, "tropical"), (3, "mild_seasonal"), (4, "generic")]
    centroids: Dict[int, Dict[str, Any]] = {}
    for archetype_id, kind in kinds:
        df = synthetic_profile(kind, month=7)
        centroids[archetype_id] = {
            "name": kind,
            "avg_temp": float(df["temperature_c"].mean()),
            "avg_rh": float(df["relative_humidity_percent"].mean()),
            "peak_solar": float(df["solar_w_m2"].max()),
            "rh_range": float(df["relative_humidity_percent"].max() - df["relative_humidity_percent"].min()),
        }
    return centroids


ARCHETYPE_CENTROIDS = _archetype_centroids()


def _climate_feature_vector(climate: pd.DataFrame) -> np.ndarray:
    return np.array(
        [
            float(climate["temperature_c"].mean()),
            float(climate["relative_humidity_percent"].mean()),
            float(climate["solar_w_m2"].max()) / 50.0,
            float(climate["relative_humidity_percent"].max() - climate["relative_humidity_percent"].min()),
        ]
    )


def classify_climate_archetype(climate: pd.DataFrame) -> Tuple[int, str, float]:
    """Nearest-centroid archetype classification with a normalized out-of-distribution distance."""
    centroid_vectors = {
        archetype_id: np.array(
            [c["avg_temp"], c["avg_rh"], c["peak_solar"] / 50.0, c["rh_range"]]
        )
        for archetype_id, c in ARCHETYPE_CENTROIDS.items()
    }
    matrix = np.array(list(centroid_vectors.values()))
    scale = matrix.std(axis=0)
    scale[scale == 0] = 1.0

    point = _climate_feature_vector(climate)
    distances = {
        archetype_id: float(np.linalg.norm((point - vec) / scale))
        for archetype_id, vec in centroid_vectors.items()
    }
    best_id = min(distances, key=distances.get)
    return best_id, ARCHETYPE_CENTROIDS[best_id]["name"], distances[best_id]


def _circular_window_bounds(hours: List[int]) -> Tuple[int, int]:
    """Return (start_hour, end_hour) for a contiguous circular block of hours, handling midnight wraparound."""
    hour_set = set(int(h) for h in hours)
    n = len(hour_set)
    if n == 0:
        return 0, 0
    for start in range(24):
        block = [(start + i) % 24 for i in range(n)]
        if set(block) == hour_set:
            return start, (start + n) % 24
    sorted_hours = sorted(hour_set)
    return sorted_hours[0], (sorted_hours[-1] + 1) % 24


def _format_hour(hour: int) -> str:
    return f"{hour % 24:02d}:00"


def build_recommended_schedule(schedule: pd.DataFrame, top_row: pd.Series) -> Dict[str, Any]:
    capture_hours = schedule.loc[schedule["action"] == "Capture", "hour"].astype(int).tolist()
    release_hours = schedule.loc[schedule["action"] == "Release + condense", "hour"].astype(int).tolist()
    adsorb_start, adsorb_end = _circular_window_bounds(capture_hours)
    regen_start, regen_end = _circular_window_bounds(release_hours)
    return {
        "adsorb_start": _format_hour(adsorb_start),
        "adsorb_end": _format_hour(adsorb_end),
        "regenerate_start": _format_hour(regen_start),
        "regenerate_end": _format_hour(regen_end),
        "T_regen_C": round(float(top_row["regen_temp_c"]), 1),
    }, release_hours


def estimate_energy_and_cost(
    top_row: pd.Series,
    climate: pd.DataFrame,
    release_hours: List[int],
    mass_kg: float,
    energy_source: str,
) -> Dict[str, float]:
    release_df = climate[climate["hour"].isin(release_hours)]
    ambient_desorb_temp = float(release_df["temperature_c"].mean()) if len(release_df) else float(
        climate["temperature_c"].mean()
    )
    delta_t = max(float(top_row["regen_temp_c"]) - ambient_desorb_temp, 5.0)

    water_removed_kg = mass_kg * float(top_row["predicted_working_capacity_kgkg"])
    thermal_kj = (
        mass_kg * SORBENT_SPECIFIC_HEAT_KJ_PER_KGK * delta_t
        + water_removed_kg * WATER_DESORPTION_ENTHALPY_KJ_PER_KG
    )
    energy_per_cycle_kwh = thermal_kj / 3600.0
    daily_energy_kwh = energy_per_cycle_kwh * float(top_row["cycles_day"])

    yield_lpd = max(float(top_row["estimated_liters_day"]), 1e-6)
    energy_kwh_per_l = daily_energy_kwh / yield_lpd

    material_cost_day = mass_kg * float(top_row["cycles_day"]) * (
        (1.0 - float(top_row["cost_score"])) * MATERIAL_COST_FACTOR_PER_KG_CYCLE
    )
    energy_cost_day = daily_energy_kwh * ENERGY_COST_PER_KWH.get(energy_source, 0.10)
    cost_per_l = (material_cost_day + energy_cost_day) / yield_lpd

    available_energy_kwh_day = None
    if energy_source == "Solar only":
        solar_wh = float(release_df["solar_w_m2"].sum())
        available_energy_kwh_day = (
            solar_wh * COLLECTOR_AREA_M2_PER_KG * mass_kg * SOLAR_COLLECTION_EFFICIENCY / 1000.0
        )

    return {
        "energy_kwh_per_l": energy_kwh_per_l,
        "cost_per_l": cost_per_l,
        "daily_energy_kwh": daily_energy_kwh,
        "available_energy_kwh_day": available_energy_kwh_day,
    }


def evaluate_decision(
    top_row: pd.Series,
    target_liters_day: float,
    alternative_cost_per_l: float,
    cost_per_l: float,
    energy_info: Dict[str, float],
    ood_z: float,
) -> Dict[str, Any]:
    reasons: List[str] = []
    yield_lpd = float(top_row["estimated_liters_day"])
    ci_low = float(top_row["yield_low_liters_day"])
    ci_high = float(top_row["yield_high_liters_day"])
    ci_width_relative = (ci_high - ci_low) / max(yield_lpd, 1e-6)

    if ci_low < target_liters_day:
        reasons.append(
            f"Lower 90% confidence bound ({ci_low:.2f} L/day) is below the {target_liters_day:.2f} L/day target."
        )
    if cost_per_l > alternative_cost_per_l:
        reasons.append(
            f"Estimated cost (${cost_per_l:.2f}/L) exceeds the stated alternative-water cost "
            f"(${alternative_cost_per_l:.2f}/L)."
        )
    available = energy_info["available_energy_kwh_day"]
    if available is not None and energy_info["daily_energy_kwh"] > available:
        reasons.append(
            f"Required regeneration energy ({energy_info['daily_energy_kwh']:.2f} kWh/day) exceeds "
            f"available energy for this energy mode ({available:.2f} kWh/day)."
        )
    if ci_width_relative > CI_WIDTH_RELATIVE_THRESHOLD:
        reasons.append(
            f"Prediction interval spans {ci_width_relative * 100:.0f}% of the point estimate, above the "
            f"{CI_WIDTH_RELATIVE_THRESHOLD * 100:.0f}% documented confidence threshold."
        )
    if ood_z > OOD_Z_THRESHOLD:
        reasons.append(
            f"Climate profile is {ood_z:.1f} normalized units from the nearest known archetype "
            "(out-of-distribution site)."
        )

    decision = "DO NOT DEPLOY" if reasons else "VIABLE"
    return {"decision": decision, "reasons": reasons, "ci_width_relative": ci_width_relative}


def build_explanation(
    top_row: pd.Series,
    runner_row: Optional[pd.Series],
    decision_info: Dict[str, Any],
    archetype_name: str,
) -> List[str]:
    bullets: List[str] = []
    if runner_row is not None:
        yield_diff = float(top_row["estimated_liters_day"]) - float(runner_row["estimated_liters_day"])
        climate_label = archetype_name.replace("_", " ")
        if yield_diff >= 0:
            bullets.append(
                f"{top_row['short_name']} predicts {yield_diff:.2f} L/day more than runner-up "
                f"{runner_row['short_name']} in this {climate_label} climate."
            )
        else:
            bullets.append(
                f"{top_row['short_name']} yields {abs(yield_diff):.2f} L/day less than "
                f"{runner_row['short_name']} but is favored on regeneration feasibility and cost."
            )
        fit_diff = float(top_row["climate_fit_score"]) - float(runner_row["climate_fit_score"])
        if fit_diff > 0:
            bullets.append(
                f"Better climate-fit match ({top_row['climate_fit_score']:.2f} vs "
                f"{runner_row['climate_fit_score']:.2f}) between isotherm RH50 and the site's capture-window humidity."
            )
        regen_diff = float(runner_row["regen_temp_c"]) - float(top_row["regen_temp_c"])
        if regen_diff > 0:
            bullets.append(
                f"Requires {regen_diff:.0f} C lower regeneration temperature than "
                f"{runner_row['short_name']}, easing heat-source constraints."
            )
    bullets.append(
        f"Confidence: {top_row['confidence']} ({float(top_row['evidence_score']) * 100:.0f}% evidence score); "
        f"{top_row['limitation']}"
    )
    if decision_info["decision"] == "DO NOT DEPLOY":
        bullets.extend(decision_info["reasons"])
    return bullets


def build_alternative_materials(ranking: pd.DataFrame, top_name: str, limit: int = 2) -> List[Dict[str, Any]]:
    alternatives: List[Dict[str, Any]] = []
    for _, row in ranking.iterrows():
        if row["name"] == top_name:
            continue
        alternatives.append(
            {
                "material": row["short_name"],
                "yield_lpd": round(float(row["estimated_liters_day"]), 2),
                "yield_90_ci": [
                    round(float(row["yield_low_liters_day"]), 2),
                    round(float(row["yield_high_liters_day"]), 2),
                ],
                "confidence": row["confidence"],
            }
        )
        if len(alternatives) >= limit:
            break
    return alternatives


def build_ai_decision(
    climate: pd.DataFrame,
    ranking: pd.DataFrame,
    schedule: pd.DataFrame,
    mass_kg: float,
    target_liters_day: float,
    energy_source: str,
    alternative_cost_per_l: float,
) -> Dict[str, Any]:
    top = ranking.iloc[0]
    runner = ranking.iloc[1] if len(ranking) > 1 else None

    archetype_id, archetype_name, ood_z = classify_climate_archetype(climate)
    recommended_schedule, release_hours = build_recommended_schedule(schedule, top)
    energy_info = estimate_energy_and_cost(top, climate, release_hours, mass_kg, energy_source)
    decision_info = evaluate_decision(
        top, target_liters_day, alternative_cost_per_l, energy_info["cost_per_l"], energy_info, ood_z
    )
    explanation = build_explanation(top, runner, decision_info, archetype_name)
    alternative_materials = build_alternative_materials(ranking, top["name"])

    return {
        "decision": decision_info["decision"],
        "decision_reasons": decision_info["reasons"],
        "material": top["short_name"],
        "climate_archetype": archetype_id,
        "climate_archetype_name": archetype_name,
        "ood_distance": round(float(ood_z), 2),
        "yield_lpd": round(float(top["estimated_liters_day"]), 2),
        "yield_90_ci": [
            round(float(top["yield_low_liters_day"]), 2),
            round(float(top["yield_high_liters_day"]), 2),
        ],
        "energy_kwh_per_l": round(energy_info["energy_kwh_per_l"], 3),
        "cost_per_l": round(energy_info["cost_per_l"], 3),
        "runner_up": runner["short_name"] if runner is not None else None,
        "alternative_materials": alternative_materials,
        "recommended_schedule": recommended_schedule,
        "explanation": explanation,
    }
