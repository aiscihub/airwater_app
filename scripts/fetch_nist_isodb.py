"""Offline ETL step 1: pull raw water isotherms for a curated MOF list from NIST ISODB.

Run this manually when the curated material list changes. It writes raw JSON
straight from the API into data/raw/nist_isodb/ so the normalization step
(build_water_isotherms.py) is reproducible without re-hitting the network.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "nist_isodb"
RAW_DIR.mkdir(parents=True, exist_ok=True)

API = "https://adsorption.nist.gov/isodb/api"
WATER_INCHIKEY = "XLYOFNOQVPJJNP-UHFFFAOYSA-N"

# Curated from a manual survey of NIST ISODB (2026-08): searched the full
# isotherms.json index for category="exp", single-component water isotherms,
# then cross-referenced material names against known water-harvesting MOF
# literature. Selection favors materials with (a) multiple isotherms/temperatures
# and (b) coverage across low/moderate/high relative-humidity uptake steps.
# MOF-303 / MOF-801 / MOF-841 were searched for and are NOT present in ISODB's
# digitized water isotherms as of this survey -- they stay out of this pipeline
# and are handled as Tier C / exploratory elsewhere.
MATERIALS = {
    "NIST-MATDB-fa9eafbe42016145ad7734b8daead8fa": "MIL-160",
    "NIST-MATDB-dc6ca373e94306ed311a0cdae4c78ec8": "Aluminum fumarate",
    "NIST-MATDB-398100fdfee2739b63544b66842c41ab": "CAU-10-H",
    "NIST-MATDB-9ab18a5aa1db0a6827d18e1707055261": "Ni-DOBDC",
    "NIST-MATDB-b425ac967ceb23a458c5e6f13f9dee6b": "UiO-66-NH2",
    "NIST-MATDB-5ab0dbe0639729711750ef4b97715f0f": "UiO-66",
    "NIST-MATDB-bb2eb3bdcf42c2df897332bd449f4b88": "DMOF-1",
    "NIST-MATDB-48f4023d446be2a618f2ac6acc88fee2": "MIL-100(Cr)",
    "NIST-MATDB-6077f48427dfa5fd3f8b6a40b13473a3": "MIL-101",
    "NIST-MATDB-991daf7313251e7e607e2bab2da57e33": "CuBTC",
    "NIST-MATDB-bca0cbf6796ccfef68412bc43aeeb385": "SIFSIX-Zn (Zn(pyz)2(SiF6))",
    "NIST-MATDB-29da3034f56a3f314e86df224086cae2": "Y-fum-fcu-MOF",
}


def fetch_json(url: str, retries: int = 3, delay: float = 0.4) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "airwater-app-etl/1.0"})
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(delay)
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def main() -> None:
    index_path = RAW_DIR / "_isotherms_index.json"
    if index_path.exists():
        print(f"using cached master index: {index_path}")
        index = json.loads(index_path.read_text())
    else:
        print("downloading master isotherms index (~18MB)...")
        index = fetch_json(f"{API}/isotherms.json")
        index_path.write_text(json.dumps(index))
        print(f"cached master index: {len(index)} entries")

    single_water = [
        x
        for x in index
        if x["category"] == "exp"
        and len(x["adsorbates"]) == 1
        and x["adsorbates"][0]["InChIKey"] == WATER_INCHIKEY
        and x["adsorbent"]["hashkey"] in MATERIALS
    ]
    print(f"{len(single_water)} candidate water isotherms across {len(MATERIALS)} materials")

    manifest = []
    for entry in single_water:
        filename = entry["filename"]
        out_path = RAW_DIR / f"{filename}.json"
        if not out_path.exists():
            data = fetch_json(f"{API}/isotherm/{filename}.json")
            out_path.write_text(json.dumps(data, indent=2))
            time.sleep(0.15)
        manifest.append(
            {
                "filename": filename,
                "material_hashkey": entry["adsorbent"]["hashkey"],
                "material_name": MATERIALS[entry["adsorbent"]["hashkey"]],
                "temperature_K": entry["temperature"],
                "DOI": entry["DOI"],
            }
        )
        print(f"  ok  {filename}  ({MATERIALS[entry['adsorbent']['hashkey']]}, {entry['temperature']} K)")

    (RAW_DIR / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {len(manifest)} raw isotherms + manifest to {RAW_DIR}")


if __name__ == "__main__":
    main()
