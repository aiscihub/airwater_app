"""Offline ETL step 3: fit per-material sigmoid descriptors from real isotherms
and assemble the candidate MOF table + a provenance manifest.

Reads data/water_isotherms.csv (from build_water_isotherms.py) and writes:
  - data/candidate_mofs.csv        (used live by airwater/selector.py)
  - data/provenance_manifest.json  (per-material sourcing + evidence audit trail)

What's fit from real data: max_uptake_kgkg, rh50_percent, steepness (the
sigmoid uptake-vs-RH descriptors), pooled across all measured temperatures
for that material -- a documented simplification, not a full temperature-
dependent isotherm model. Everything fit this way is marked
evidence_source="nist_isodb_fit" below.

What's NOT available from adsorption isotherms at all: regeneration
temperature, cycle time, hydrolytic-stability score, cost, pore volume,
surface area. NIST ISODB's water-isotherm records don't carry these. For
the 3 materials that were already in the app (MIL-160, CAU-10-H,
UiO-66-NH2) the prior placeholder values are kept. For the 9 newly added
materials these are literature-informed engineering estimates by MOF
family, NOT digitized measurements -- marked evidence_source="estimated"
and should be replaced with cited values before any real science claim
is made from them.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[1]
ISOTHERMS_PATH = ROOT / "data" / "water_isotherms.csv"
OUT_CSV = ROOT / "data" / "candidate_mofs.csv"
OUT_MANIFEST = ROOT / "data" / "provenance_manifest.json"

# Non-isotherm descriptors. tier="A" = real NIST ISODB water isotherm backs
# rh50/steepness/max_uptake. tier="C" = no isotherm in this pipeline at all
# (kept from the app's original synthetic prototype descriptors).
NON_ISOTHERM_FIELDS = {
    # existing materials -- keep prior values, now explicitly sourced
    "MIL-160": dict(regen_temp_c=75, cycle_minutes=60, water_stability_score=0.82, cost_score=0.75,
                     pore_volume_cm3g=0.43, surface_area_m2g=1050, evidence_source="prior_placeholder"),
    "CAU-10-H": dict(regen_temp_c=80, cycle_minutes=75, water_stability_score=0.78, cost_score=0.73,
                      pore_volume_cm3g=0.39, surface_area_m2g=900, evidence_source="prior_placeholder"),
    "UiO-66-NH2": dict(regen_temp_c=90, cycle_minutes=110, water_stability_score=0.84, cost_score=0.58,
                        pore_volume_cm3g=0.34, surface_area_m2g=850, evidence_source="prior_placeholder"),
    # newly added materials -- literature-informed by MOF family, not measured
    "Aluminum fumarate": dict(regen_temp_c=75, cycle_minutes=55, water_stability_score=0.85, cost_score=0.78,
                               pore_volume_cm3g=0.45, surface_area_m2g=1150, evidence_source="estimated"),
    "Ni-DOBDC": dict(regen_temp_c=110, cycle_minutes=90, water_stability_score=0.55, cost_score=0.45,
                      pore_volume_cm3g=0.58, surface_area_m2g=1350, evidence_source="estimated"),
    "UiO-66": dict(regen_temp_c=95, cycle_minutes=90, water_stability_score=0.90, cost_score=0.55,
                    pore_volume_cm3g=0.45, surface_area_m2g=1100, evidence_source="estimated"),
    "DMOF-1": dict(regen_temp_c=90, cycle_minutes=80, water_stability_score=0.40, cost_score=0.50,
                    pore_volume_cm3g=0.50, surface_area_m2g=1650, evidence_source="estimated"),
    "MIL-100(Cr)": dict(regen_temp_c=100, cycle_minutes=100, water_stability_score=0.85, cost_score=0.40,
                         pore_volume_cm3g=1.00, surface_area_m2g=1900, evidence_source="estimated"),
    "MIL-101": dict(regen_temp_c=100, cycle_minutes=110, water_stability_score=0.82, cost_score=0.35,
                     pore_volume_cm3g=1.50, surface_area_m2g=3500, evidence_source="estimated"),
    "CuBTC": dict(regen_temp_c=105, cycle_minutes=90, water_stability_score=0.35, cost_score=0.60,
                   pore_volume_cm3g=0.78, surface_area_m2g=1700, evidence_source="estimated"),
    "SIFSIX-Zn (Zn(pyz)2(SiF6))": dict(regen_temp_c=65, cycle_minutes=40, water_stability_score=0.75, cost_score=0.50,
                                        pore_volume_cm3g=0.30, surface_area_m2g=400, evidence_source="estimated"),
    "Y-fum-fcu-MOF": dict(regen_temp_c=85, cycle_minutes=70, water_stability_score=0.80, cost_score=0.35,
                           pore_volume_cm3g=0.50, surface_area_m2g=1000, evidence_source="estimated"),
}

SHORT_NAMES = {
    "Aluminum fumarate": "Al-Fumarate",
    "SIFSIX-Zn (Zn(pyz)2(SiF6))": "SIFSIX-Zn",
    "Y-fum-fcu-MOF": "Y-fum-fcu-MOF",
    "MIL-100(Cr)": "MIL-100(Cr)",
}

METAL_FAMILY = {
    "MIL-160": "Al", "CAU-10-H": "Al", "UiO-66-NH2": "Zr", "Aluminum fumarate": "Al",
    "Ni-DOBDC": "Ni", "UiO-66": "Zr", "DMOF-1": "Zn", "MIL-100(Cr)": "Cr",
    "MIL-101": "Cr", "CuBTC": "Cu", "SIFSIX-Zn (Zn(pyz)2(SiF6))": "Zn", "Y-fum-fcu-MOF": "Y",
}

# MOF-303/MOF-801/MOF-841: not present in NIST ISODB's digitized water
# isotherms (searched full 39,824-entry master index, no match). Kept as
# Tier C / exploratory using the app's original synthetic prototype curves
# so their existing material images and name-recognition aren't lost, but
# clearly excluded from the "real data" evidence tier.
TIER_C_LEGACY = pd.DataFrame(
    [
        dict(name="MOF-303", short_name="MOF-303", metal_family="Al", max_uptake_kgkg=0.42, rh50_percent=22,
             steepness=0.16, regen_temp_c=85, cycle_minutes=20, water_stability_score=0.88, cost_score=0.72,
             pore_volume_cm3g=0.52, surface_area_m2g=1250,
             notes="No water isotherm found in NIST ISODB; kept as exploratory (Tier C) using original prototype descriptors.",
             source_hint="ACS Central Science 2019; ACS Central Science review 2020"),
        dict(name="MOF-801", short_name="MOF-801", metal_family="Zr", max_uptake_kgkg=0.28, rh50_percent=25,
             steepness=0.13, regen_temp_c=65, cycle_minutes=90, water_stability_score=0.86, cost_score=0.55,
             pore_volume_cm3g=0.48, surface_area_m2g=990,
             notes="No water isotherm found in NIST ISODB; kept as exploratory (Tier C) using original prototype descriptors.",
             source_hint="Science 2017; Science Advances 2018"),
        dict(name="MOF-841", short_name="MOF-841", metal_family="Zr", max_uptake_kgkg=0.50, rh50_percent=45,
             steepness=0.09, regen_temp_c=95, cycle_minutes=120, water_stability_score=0.80, cost_score=0.48,
             pore_volume_cm3g=0.68, surface_area_m2g=1450,
             notes="No water isotherm found in NIST ISODB; kept as exploratory (Tier C) using original prototype descriptors.",
             source_hint="Water adsorption literature"),
    ]
)
for col, val in [("tier", "C"), ("evidence_source", "prior_placeholder_no_isotherm"), ("n_isotherms", 0),
                  ("n_points", 0), ("n_papers", 0), ("temperature_coverage_k", ""), ("doi_list", ""),
                  ("data_quality_flags", "no_nist_isotherm")]:
    TIER_C_LEGACY[col] = val

# Evidence score must reflect real measured support only. These materials
# have zero NIST water isotherms and zero backing papers, so their score has
# to sit below every real (Tier A/B) material's isotherm-backed score, not
# above it -- the prior values (0.90/0.92/0.70) were unchanged leftovers from
# the pre-real-data synthetic build and let no-evidence materials outrank
# materials with real measurements in downstream ranking.
TIER_C_LEGACY["evidence_score"] = 0.05


def sigmoid(rh, max_uptake, rh50, k):
    return max_uptake / (1.0 + np.exp(-k * (rh - rh50)))


def fit_material(rh: np.ndarray, uptake: np.ndarray) -> tuple[float, float, float, float]:
    uptake_cap = max(float(uptake.max()) * 1.15, 0.05)

    def resid(params):
        return sigmoid(rh, *params) - uptake

    best = None
    for rh50_guess in (10, 25, 40, 55, 70):
        p0 = [float(uptake.max()), rh50_guess, 0.15]
        try:
            result = least_squares(
                resid, p0, loss="soft_l1", f_scale=0.02,
                bounds=([uptake.max() * 0.7, 0.5, 0.01], [uptake_cap, 99.5, 3.0]),
            )
        except Exception:
            continue
        if best is None or result.cost < best.cost:
            best = result
    max_uptake, rh50, k = best.x
    pred = sigmoid(rh, max_uptake, rh50, k)
    ss_res = float(np.sum((uptake - pred) ** 2))
    ss_tot = float(np.sum((uptake - uptake.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(max_uptake), float(rh50), float(k), r2


def calibrate_interval(rh: np.ndarray, uptake: np.ndarray, seed: int = 11) -> dict:
    """5th/95th percentile residual band for this material's uptake predictions.

    With >=20 points, fit on a random 80% and take residual quantiles on the
    held-out 20% -- an honest out-of-sample interval, not the fit's own
    (optimistically narrow) in-sample residuals. Below that point count a
    held-out split isn't meaningful, so we fall back to in-sample residuals
    and say so explicitly rather than pretending the same rigor.
    """
    n = len(rh)
    rng = np.random.default_rng(seed)
    if n >= 20:
        idx = rng.permutation(n)
        split = int(n * 0.8)
        train_idx, test_idx = idx[:split], idx[split:]
        max_uptake, rh50, k, _ = fit_material(rh[train_idx], uptake[train_idx])
        pred = sigmoid(rh[test_idx], max_uptake, rh50, k)
        residuals = uptake[test_idx] - pred
        method = "holdout_20pct_residuals"
    else:
        max_uptake, rh50, k, _ = fit_material(rh, uptake)
        pred = sigmoid(rh, max_uptake, rh50, k)
        residuals = uptake - pred
        method = "in_sample_residuals_small_n_not_holdout"
    p05, p95 = float(np.percentile(residuals, 5)), float(np.percentile(residuals, 95))
    return {"resid_p05_kgkg": round(p05, 4), "resid_p95_kgkg": round(p95, 4), "interval_method": method, "n_holdout": int(n - int(n * 0.8)) if n >= 20 else n}


def evidence_score(n_papers: int, n_points: int, n_temps: int, has_quality_flag: bool) -> tuple[float, dict]:
    components = {
        "has_water_isotherm": 40,
        "independent_sources": min(20, 10 + 5 * max(0, n_papers - 1)),
        "temperature_coverage": 15 if n_temps >= 2 else (7 if n_temps == 1 else 0),
        "point_density": min(15, round(15 * min(1.0, np.log10(max(n_points, 1) + 1) / 2.0))),
        "regeneration_or_stability_data": 0,  # not available from adsorption isotherms alone
        "data_quality_penalty": -10 if has_quality_flag else 0,
    }
    total = sum(components.values())
    total_capped = max(0, min(100, total))
    return total_capped / 100.0, components


def main() -> None:
    df = pd.read_csv(ISOTHERMS_PATH)
    df = df.dropna(subset=["relative_humidity_percent", "uptake_kg_per_kg"])

    rows = []
    manifest = {}
    for material_name, g in df.groupby("material_name"):
        rh = g["relative_humidity_percent"].to_numpy(dtype=float)
        uptake = g["uptake_kg_per_kg"].to_numpy(dtype=float)
        max_uptake, rh50, k, r2 = fit_material(rh, uptake)

        n_isotherms = g["NIST_isotherm_id"].nunique()
        n_points = len(g)
        dois = sorted(g["DOI"].unique().tolist())
        n_papers = len(dois)
        temps = sorted(g["temperature_K"].unique().tolist())
        data_rh_min, data_rh_max = float(rh.min()), float(rh.max())

        source_flags = set()
        for flag_str in g["source_quality_flags"]:
            for token in str(flag_str).split(";"):
                if token in ("narrow_rh_range_needs_verification", "branch_uncertain_high_reversal") or token.startswith(
                    ("unrecognized_", "rh_exceeds_", "negative_uptake")
                ):
                    source_flags.add(token)
        if r2 < 0.6:
            source_flags.add(f"poor_sigmoid_fit_r2_{r2:.2f}")
        has_quality_flag = bool(source_flags)

        score, components = evidence_score(n_papers, n_points, len(temps), has_quality_flag)
        interval = calibrate_interval(rh, uptake)

        extra = NON_ISOTHERM_FIELDS[material_name]
        short = SHORT_NAMES.get(material_name, material_name)
        family = METAL_FAMILY.get(material_name, "")

        quality_note = ""
        if source_flags:
            quality_note = f" Data-quality flags: {', '.join(sorted(source_flags))} -- see provenance_manifest.json."

        rows.append(
            dict(
                name=material_name,
                short_name=short,
                metal_family=family,
                max_uptake_kgkg=round(max_uptake, 3),
                rh50_percent=round(rh50, 1),
                steepness=round(k, 3),
                regen_temp_c=extra["regen_temp_c"],
                cycle_minutes=extra["cycle_minutes"],
                water_stability_score=extra["water_stability_score"],
                cost_score=extra["cost_score"],
                evidence_score=round(score, 3),
                pore_volume_cm3g=extra["pore_volume_cm3g"],
                surface_area_m2g=extra["surface_area_m2g"],
                notes=(f"Fit from {n_isotherms} real NIST ISODB water isotherm(s), {n_points} points, "
                       f"R^2={r2:.2f} vs digitized data.{quality_note}"),
                source_hint="; ".join(dois),
                tier="A" if not has_quality_flag else "B",
                evidence_source="nist_isodb_fit",
                n_isotherms=n_isotherms,
                n_points=n_points,
                n_papers=n_papers,
                temperature_coverage_k=";".join(str(int(t)) for t in temps),
                doi_list=";".join(dois),
                data_quality_flags=";".join(sorted(source_flags)),
                fit_r2=round(r2, 3),
                resid_p05_kgkg=interval["resid_p05_kgkg"],
                resid_p95_kgkg=interval["resid_p95_kgkg"],
                interval_method=interval["interval_method"],
                evidence_has_isotherm=components["has_water_isotherm"],
                evidence_independent_sources=components["independent_sources"],
                evidence_temperature_coverage=components["temperature_coverage"],
                evidence_point_density=components["point_density"],
                evidence_regen_stability_data=components["regeneration_or_stability_data"],
                evidence_quality_penalty=components["data_quality_penalty"],
                data_rh_min_percent=round(data_rh_min, 1),
                data_rh_max_percent=round(data_rh_max, 1),
            )
        )

        manifest[material_name] = dict(
            fit_r2=round(r2, 3),
            n_isotherms=n_isotherms,
            n_points=n_points,
            n_papers=n_papers,
            temperatures_k=temps,
            dois=dois,
            calibrated_interval=interval,
            evidence_score=round(score, 3),
            evidence_components=components,
            non_isotherm_fields_source=extra["evidence_source"],
            data_quality_flags=sorted(source_flags) if source_flags else None,
        )

    tier_a = pd.DataFrame(rows).sort_values("name").reset_index(drop=True)
    full = pd.concat([tier_a, TIER_C_LEGACY], ignore_index=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(OUT_CSV, index=False)

    for _, row in TIER_C_LEGACY.iterrows():
        manifest[row["name"]] = dict(
            tier="C",
            note="No NIST ISODB water isotherm found; using original synthetic prototype descriptors.",
            evidence_score=float(row["evidence_score"]),
        )

    summary = dict(
        generated_from="NIST ISODB (https://adsorption.nist.gov/isodb/)",
        n_tier_a_or_b_mofs=len(tier_a),
        n_tier_c_mofs=len(TIER_C_LEGACY),
        n_unique_isotherms=int(df["NIST_isotherm_id"].nunique()),
        n_source_papers=int(df["DOI"].nunique()),
        n_measurement_points=int(len(df)),
        materials=manifest,
    )
    OUT_MANIFEST.write_text(json.dumps(summary, indent=2))

    print(full[["name", "tier", "max_uptake_kgkg", "rh50_percent", "steepness", "evidence_score", "n_isotherms", "n_points"]].to_string(index=False))
    print(f"\n-> {OUT_CSV}")
    print(f"-> {OUT_MANIFEST}")


if __name__ == "__main__":
    main()
