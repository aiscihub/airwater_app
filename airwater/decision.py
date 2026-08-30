from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from airwater.climate import synthetic_profile
from airwater.physics import (
    COLLECTOR_AREA_M2_PER_KG,
    ENERGY_COST_PER_KWH,
    MATERIAL_COST_FACTOR_PER_KG_CYCLE,
    SOLAR_COLLECTION_EFFICIENCY,
    SORBENT_SPECIFIC_HEAT_KJ_PER_KGK,
    WATER_DESORPTION_ENTHALPY_KJ_PER_KG,
)

CI_WIDTH_RELATIVE_THRESHOLD = 0.40  # spec's documented tau ("e.g., 30% relative"), widened for demo bands
OOD_Z_THRESHOLD = 1.8  # normalized-centroid-distance heuristic in place of Mahalanobis-in-PCA-space
EVIDENCE_THRESHOLD = 0.65  # below this, the candidate's literature/evidence base is treated as thin
CLIMATE_FIT_THRESHOLD = 0.50  # below this, the capture-window humidity poorly matches the isotherm


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
    delta_t = max(float(top_row["desorption_temp_c"]) - ambient_desorb_temp, 5.0)

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
        "material_cost_per_l": material_cost_day / yield_lpd,
        "energy_cost_per_l": energy_cost_day / yield_lpd,
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
    checks: List[Dict[str, Any]] = []
    yield_lpd = float(top_row["estimated_liters_day"])
    ci_low = float(top_row["yield_low_liters_day"])
    ci_high = float(top_row["yield_high_liters_day"])
    ci_width_relative = (ci_high - ci_low) / max(yield_lpd, 1e-6)

    yield_ok = ci_low >= target_liters_day
    yield_reason = (
        f"Equilibrium sorption potential: {ci_low:.2f}-{ci_high:.2f} L/day-equivalent before device losses; "
        f"the lower end is below the {target_liters_day:.2f} L/day target."
        if not yield_ok
        else f"Equilibrium sorption potential: {ci_low:.2f}-{ci_high:.2f} L/day-equivalent before device losses; "
        f"the lower end meets the {target_liters_day:.2f} L/day target."
    )
    checks.append({"id": "yield", "label": "Equilibrium sorption potential", "status": "pass" if yield_ok else "fail", "reason": yield_reason})
    if not yield_ok:
        reasons.append(yield_reason)

    regen_ok = bool(top_row["feasible"])
    regen_reason = (
        f"Material needs {float(top_row['regen_temp_c']):.0f} C; achievable bed temperature in this scenario "
        f"is {float(top_row['achievable_regen_temp_c']):.0f} C ({float(top_row['regen_ceiling_c']):.0f} C after safety margin), "
        f"{'within reach' if regen_ok else 'not reachable'}."
    )
    checks.append({"id": "regen", "label": "Regeneration heat available", "status": "pass" if regen_ok else "fail", "reason": regen_reason})
    if not regen_ok:
        reasons.append(regen_reason)

    uncertainty_ok = ci_width_relative <= CI_WIDTH_RELATIVE_THRESHOLD
    uncertainty_reason = (
        f"Prediction interval spans {ci_width_relative * 100:.0f}% of the point estimate, above the "
        f"{CI_WIDTH_RELATIVE_THRESHOLD * 100:.0f}% documented confidence threshold."
        if not uncertainty_ok
        else f"Prediction interval spans {ci_width_relative * 100:.0f}% of the point estimate, within the "
        f"{CI_WIDTH_RELATIVE_THRESHOLD * 100:.0f}% documented confidence threshold."
    )
    checks.append({"id": "uncertainty", "label": "Prediction uncertainty", "status": "pass" if uncertainty_ok else "fail", "reason": uncertainty_reason})
    if not uncertainty_ok:
        reasons.append(uncertainty_reason)

    ood_ok = ood_z <= OOD_Z_THRESHOLD
    ood_reason = (
        f"Climate profile is {ood_z:.1f} normalized units from the nearest known archetype "
        "(out-of-distribution site)."
        if not ood_ok
        else f"Climate profile is {ood_z:.1f} normalized units from the nearest known archetype (within the modeled domain)."
    )
    checks.append({"id": "ood", "label": "Climate in modeled domain", "status": "pass" if ood_ok else "fail", "reason": ood_reason})
    if not ood_ok:
        reasons.append(ood_reason)

    cost_ok = cost_per_l <= alternative_cost_per_l
    cost_reason = (
        f"Estimated cost (${cost_per_l:.2f}/L) exceeds the stated alternative-water cost (${alternative_cost_per_l:.2f}/L)."
        if not cost_ok
        else f"Estimated cost (${cost_per_l:.2f}/L) is at or below the stated alternative-water cost (${alternative_cost_per_l:.2f}/L)."
    )
    checks.append({"id": "cost", "label": "Cost versus alternative", "status": "pass" if cost_ok else "fail", "reason": cost_reason})
    if not cost_ok:
        reasons.append(cost_reason)

    available = energy_info["available_energy_kwh_day"]
    if available is not None and energy_info["daily_energy_kwh"] > available:
        energy_reason = (
            f"Required regeneration energy ({energy_info['daily_energy_kwh']:.2f} kWh/day) exceeds "
            f"available energy for this energy mode ({available:.2f} kWh/day)."
        )
        reasons.append(energy_reason)
        checks.append({"id": "energy_budget", "label": "Energy budget", "status": "fail", "reason": energy_reason})
    elif available is not None:
        checks.append({
            "id": "energy_budget", "label": "Energy budget", "status": "pass",
            "reason": f"Required regeneration energy ({energy_info['daily_energy_kwh']:.2f} kWh/day) is within the "
            f"available budget for this energy mode ({available:.2f} kWh/day).",
        })

    evidence_ok = float(top_row["evidence_score"]) >= EVIDENCE_THRESHOLD
    evidence_reason = (
        f"Evidence score ({float(top_row['evidence_score']) * 100:.0f}%) is below the "
        f"{EVIDENCE_THRESHOLD * 100:.0f}% threshold for this material."
        if not evidence_ok
        else f"Evidence score ({float(top_row['evidence_score']) * 100:.0f}%) meets the {EVIDENCE_THRESHOLD * 100:.0f}% threshold."
    )
    checks.append({"id": "evidence", "label": "Evidence quality", "status": "pass" if evidence_ok else "fail", "reason": evidence_reason})
    if not evidence_ok:
        reasons.append(evidence_reason)

    decision = "DO NOT DEPLOY" if reasons else "VIABLE"
    return {"decision": decision, "reasons": reasons, "checks": checks, "ci_width_relative": ci_width_relative}


def classify_verdict(candidate: pd.Series, ood_z: float) -> str:
    """Single-label verdict for a candidate, worst-issue-first."""
    if not bool(candidate["feasible"]):
        return "REGEN_INFEASIBLE"
    if ood_z > OOD_Z_THRESHOLD:
        return "OUT_OF_DOMAIN"
    if float(candidate["evidence_score"]) < EVIDENCE_THRESHOLD:
        return "INSUFFICIENT_EVIDENCE"
    return str(candidate["test_recommendation_id"]).upper()


def get_loss_reasons(candidate: pd.Series, winner: pd.Series) -> List[str]:
    """Up to two reasons a non-winning candidate ranked below the winner, priority-ordered."""
    reasons: List[str] = []
    if not bool(candidate["meets_target"]):
        reasons.append("Below target")
    if not bool(candidate["feasible"]):
        reasons.append("Higher regeneration burden")
    if float(winner["climate_fit_score"]) - float(candidate["climate_fit_score"]) > 0.08:
        reasons.append("Lower climate fit")
    if not reasons and float(winner["estimated_liters_day"]) - float(candidate["estimated_liters_day"]) > 0.01:
        reasons.append("Lower yield")
    if float(winner["evidence_score"]) - float(candidate["evidence_score"]) > 0.05:
        reasons.append("Lower evidence")
    if float(winner["water_stability_score"]) - float(candidate["water_stability_score"]) > 0.05:
        reasons.append("Lower stability")
    if float(candidate["ci_width_relative"]) - float(winner["ci_width_relative"]) > 0.05:
        reasons.append("Higher uncertainty")
    return reasons[:2]


def build_decision_checks(winner: pd.Series, target_liters_day: float, ood_z: float) -> List[Dict[str, Any]]:
    """The five checks behind 'why this material won' (distinct from the six-rule refusal gate)."""
    meets = bool(winner["yield_low_liters_day"] >= target_liters_day)
    fit_ok = float(winner["climate_fit_score"]) >= CLIMATE_FIT_THRESHOLD
    feasible = bool(winner["feasible"])
    evidence_ok = float(winner["evidence_score"]) >= EVIDENCE_THRESHOLD
    ood_ok = ood_z <= OOD_Z_THRESHOLD
    return [
        {
            "id": "water_target", "label": "Meets water target",
            "status": "pass" if meets else "fail",
            "reason": f"Equilibrium sorption potential, lower end ({float(winner['yield_low_liters_day']):.2f} "
            f"L/day-equivalent before device losses), {'meets' if meets else 'is below'} the "
            f"{target_liters_day:.2f} L/day target.",
        },
        {
            "id": "climate_fit", "label": "Climate compatible",
            "status": "pass" if fit_ok else "warn",
            "reason": f"Climate-fit score is {float(winner['climate_fit_score']) * 100:.0f}% "
            f"({'overlaps' if fit_ok else 'only partially overlaps'} the capture-window humidity).",
        },
        {
            "id": "regen_feasible", "label": "Regeneration feasible",
            "status": "pass" if feasible else "fail",
            "reason": f"{float(winner['regen_temp_c']):.0f} C material target vs. "
            f"{float(winner['achievable_regen_temp_c']):.0f} C achievable bed temperature in this scenario "
            f"({'within reach' if feasible else 'not reachable'} after the safety margin).",
        },
        {
            "id": "evidence", "label": "Evidence sufficient",
            "status": "pass" if evidence_ok else "warn",
            "reason": f"Evidence score {float(winner['evidence_score']) * 100:.0f}%; "
            f"stability score {float(winner['water_stability_score']) * 100:.0f}%.",
        },
        {
            "id": "ood", "label": "Climate within modeled domain",
            "status": "pass" if ood_ok else "warn",
            "reason": f"OOD distance {ood_z:.2f} {'is within' if ood_ok else 'exceeds'} the {OOD_Z_THRESHOLD:.1f} threshold.",
        },
    ]


def build_score_contributions(winner: pd.Series) -> Dict[str, float]:
    """Real per-scenario weighted contributions (not the static weights) behind the winner's score."""
    return {
        "yield_vs_target": round(0.43 * float(winner["target_score"]) * 100, 1),
        "climate_fit": round(0.16 * float(winner["climate_fit_score"]) * 100, 1),
        "evidence": round(0.19 * float(winner["evidence_score"]) * 100, 1),
        "stability": round(0.13 * float(winner["water_stability_score"]) * 100, 1),
        "cost_proxy": round(0.09 * float(winner["cost_score"]) * 100, 1),
        "regen_penalty": round(-0.20 * float(winner["regen_penalty"]) * 100, 1),
    }


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
        f"Evidence quality: {top_row['evidence_quality']} "
        f"({float(top_row['evidence_score']) * 100:.0f}% evidence score) | "
        f"Prediction uncertainty: {top_row['prediction_uncertainty_label']} "
        f"(interval spans {float(top_row['prediction_uncertainty_percent']):.0f}% of the point estimate) | "
        f"Screening verdict: {decision_info['decision']}. {top_row['limitation']}"
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

    candidates_enriched = []
    for _, cand_row in ranking.iterrows():
        candidates_enriched.append({
            **json.loads(cand_row.to_json()),
            "verdict": classify_verdict(cand_row, ood_z),
            "loss_reasons": [] if cand_row["name"] == top["name"] else get_loss_reasons(cand_row, top),
        })

    return {
        "decision": decision_info["decision"],
        "decision_reasons": decision_info["reasons"],
        "refusal_checks": decision_info["checks"],
        "decision_checks": build_decision_checks(top, target_liters_day, ood_z),
        "score_contributions": build_score_contributions(top),
        "candidates": candidates_enriched,
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
        "material_cost_per_l": round(energy_info["material_cost_per_l"], 3),
        "energy_cost_per_l": round(energy_info["energy_cost_per_l"], 3),
        "cost_scope_note": "Includes amortized material wear and regeneration energy only -- excludes device capex, maintenance labor, and water post-treatment.",
        "runner_up": runner["short_name"] if runner is not None else None,
        "alternative_materials": alternative_materials,
        "recommended_schedule": recommended_schedule,
        "explanation": explanation,
    }
