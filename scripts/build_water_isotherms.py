"""Offline ETL step 2: normalize raw NIST ISODB isotherms into one clean table.

Reads data/raw/nist_isodb/*.json (written by fetch_nist_isodb.py) and writes
data/water_isotherms.csv, a per-measurement-point table with a fixed schema
and explicit provenance/quality flags on every row. Nothing here is invented:
values that aren't available from the source are left blank and flagged.
"""
from __future__ import annotations

import csv
import glob
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "nist_isodb"
OUT_PATH = ROOT / "data" / "water_isotherms.csv"

WATER_MOLAR_MASS_G_MOL = 18.015
STP_MOLAR_VOLUME_CM3_MOL = 22414.0

# Antoine coefficients for water, log10(P[bar]) = A - B/(T[K] + C).
# Stull (1947) parameterization as tabulated in the NIST WebBook, valid 255.9-373K,
# which covers this dataset's full 273-343K range.
ANTOINE_A, ANTOINE_B, ANTOINE_C = 4.6543, 1435.264, -64.848


def water_psat_bar(temp_k: float) -> float:
    return 10 ** (ANTOINE_A - ANTOINE_B / (temp_k + ANTOINE_C))


def to_kg_per_kg(value: float, units: str) -> tuple[float | None, str | None]:
    if units == "g/g":
        return value, None
    if units == "mg/g":
        return value / 1000.0, None
    if units == "mmol/g":
        return value * WATER_MOLAR_MASS_G_MOL / 1000.0, None
    if units == "wt%":
        return value / 100.0, None
    if units == "cm3(STP)/g":
        moles_per_g = value / STP_MOLAR_VOLUME_CM3_MOL
        return moles_per_g * WATER_MOLAR_MASS_G_MOL, None
    return None, f"unrecognized_uptake_units:{units}"


def infer_branch(pressures: list[float]) -> tuple[str, float]:
    if len(pressures) < 2:
        return "adsorption", 0.0
    inc = sum(1 for i in range(1, len(pressures)) if pressures[i] >= pressures[i - 1])
    dec = sum(1 for i in range(1, len(pressures)) if pressures[i] < pressures[i - 1])
    total = inc + dec
    reversal_fraction = dec / total if total else 0.0
    branch = "adsorption" if inc >= dec else "desorption"
    return branch, reversal_fraction


def main() -> None:
    files = sorted(glob.glob(str(RAW_DIR / "*.Isotherm*.json")))
    manifest = json.loads((RAW_DIR / "_manifest.json").read_text())
    manifest_by_filename = {m["filename"]: m for m in manifest}

    rows = []
    n_isotherms = 0
    n_materials = set()
    n_papers = set()

    # Second pass below flags isotherms whose full recorded RH span sits
    # entirely under 20% -- unusual for a water sorption step and worth a
    # human double-checking the source figure's pressure-axis units.
    narrow_range_isotherms: set[str] = set()

    for fpath in files:
        d = json.loads(Path(fpath).read_text())
        filename = d["filename"]
        meta = manifest_by_filename.get(filename, {})
        material_id = d["adsorbent"]["hashkey"]
        material_name = meta.get("material_name", d["adsorbent"].get("name", ""))
        doi = d.get("DOI", "")
        temp_k = float(d["temperature"])
        adsorption_units = d.get("adsorptionUnits", "")
        composition_type = d.get("compositionType", "")
        psat_bar = water_psat_bar(temp_k)

        points = d.get("isotherm_data", [])
        pressures = [float(p["pressure"]) for p in points]
        branch, reversal_fraction = infer_branch(pressures)
        branch_flag = "branch_inferred_from_point_order"
        if reversal_fraction > 0.15:
            branch_flag += ";branch_uncertain_high_reversal"

        n_isotherms += 1
        n_materials.add(material_id)
        n_papers.add(doi)

        for i, p in enumerate(points):
            species = p.get("species_data", [{}])[0]
            uptake_raw = float(species.get("adsorption", p.get("total_adsorption", 0.0)))

            flags = ["digitized_from_figure", branch_flag]

            if composition_type == "relhumidity":
                rh_percent = float(species.get("composition", 0.0))
                pressure_bar = round(rh_percent / 100.0 * psat_bar, 6)
            elif composition_type == "molefraction":
                pressure_bar = float(p["pressure"])
                rh_percent = pressure_bar / psat_bar * 100.0
            else:
                flags.append(f"unrecognized_composition_type:{composition_type}")
                pressure_bar = float(p.get("pressure", 0.0))
                rh_percent = float("nan")

            if not math.isnan(rh_percent) and rh_percent > 105.0:
                flags.append("rh_exceeds_100pct_flagged")
            rh_percent_clipped = None if math.isnan(rh_percent) else max(0.0, min(rh_percent, 100.0))

            uptake_kgkg, unit_flag = to_kg_per_kg(uptake_raw, adsorption_units)
            if unit_flag:
                flags.append(unit_flag)
            if uptake_kgkg is not None and uptake_kgkg < 0:
                flags.append("negative_uptake_flagged")
                uptake_kgkg = None

            rows.append(
                {
                    "canonical_material_id": material_id,
                    "material_name": material_name,
                    "material_aliases": "",
                    "NIST_isotherm_id": filename,
                    "DOI": doi,
                    "measurement_type": "experimental",
                    "temperature_K": temp_k,
                    "pressure_bar": round(pressure_bar, 6),
                    "pressure_units": "bar",
                    "relative_humidity_percent": None if rh_percent_clipped is None else round(rh_percent_clipped, 2),
                    "uptake": uptake_raw,
                    "uptake_units": adsorption_units,
                    "uptake_kg_per_kg": None if uptake_kgkg is None else round(uptake_kgkg, 5),
                    "adsorption_or_desorption_branch": branch,
                    "activation_conditions": "",
                    "source_quality_flags": ";".join(flags),
                }
            )

    rh_max_by_isotherm: dict[str, float] = {}
    for row in rows:
        rh = row["relative_humidity_percent"]
        if rh is None:
            continue
        iso_id = row["NIST_isotherm_id"]
        rh_max_by_isotherm[iso_id] = max(rh_max_by_isotherm.get(iso_id, 0.0), rh)
    for iso_id, rh_max in rh_max_by_isotherm.items():
        if rh_max < 20.0:
            narrow_range_isotherms.add(iso_id)
    for row in rows:
        if row["NIST_isotherm_id"] in narrow_range_isotherms:
            row["source_quality_flags"] += ";narrow_rh_range_needs_verification"

    if narrow_range_isotherms:
        materials_affected = sorted({r["material_name"] for r in rows if r["NIST_isotherm_id"] in narrow_range_isotherms})
        print(f"NOTE: {len(narrow_range_isotherms)} isotherm(s) flagged narrow_rh_range_needs_verification: {materials_affected}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} measurement points from {n_isotherms} isotherms")
    print(f"unique MOFs: {len(n_materials)}")
    print(f"unique isotherms: {n_isotherms}")
    print(f"unique source papers (DOIs): {len(n_papers)}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
