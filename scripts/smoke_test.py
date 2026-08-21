from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import analyze  # noqa: E402


PRESETS = {
    "Desert solar": {
        "location": "Phoenix, Arizona",
        "month": 7,
        "mass_kg": 10,
        "target_liters_day": 3,
        "max_regen_temp_c": 85,
        "energy_source": "Solar only",
        "efficiency": 0.55,
        "data_source": "Offline demo profile",
    },
    "Humid coastal": {
        "location": "Miami, Florida",
        "month": 8,
        "mass_kg": 8,
        "target_liters_day": 5,
        "max_regen_temp_c": 80,
        "energy_source": "Solar only",
        "efficiency": 0.60,
        "data_source": "Offline demo profile",
    },
    "Mild hybrid": {
        "location": "Nairobi, Kenya",
        "month": 1,
        "mass_kg": 10,
        "target_liters_day": 3,
        "max_regen_temp_c": 75,
        "energy_source": "Electricity or hybrid",
        "efficiency": 0.55,
        "data_source": "Offline demo profile",
    },
    "Tropical solar": {
        "location": "Singapore",
        "month": 6,
        "mass_kg": 6,
        "target_liters_day": 4,
        "max_regen_temp_c": 85,
        "energy_source": "Solar only",
        "efficiency": 0.60,
        "data_source": "Offline demo profile",
    },
}


def main() -> None:
    top_names: set[str] = set()
    for name, payload in PRESETS.items():
        result = analyze(payload)
        assert len(result["climate"]) == 24
        assert len(result["schedule"]) == 24
        assert len(result["ranking"]) >= 5
        assert result["ranking"][0]["score"] >= result["ranking"][-1]["score"]
        assert len(result["climate_summary"]["capture_hours"]) == 8
        assert len(result["climate_summary"]["release_hours"]) == 5
        assert result["top"]["estimated_liters_day"] >= 0
        assert result["top"]["estimated_range"].endswith("L/day")
        assert result["metrics"]["split"] == "GroupShuffleSplit by MOF name"
        assert len(result["feature_importance"]) > 0
        top_names.add(str(result["top"]["short_name"]))
        print(
            f"PASS {name}: {result['top']['short_name']} | "
            f"{result['top']['estimated_range']} | {result['top']['confidence']} confidence"
        )

    assert len(top_names) >= 2, "At least two presets should produce different top candidates."
    print(f"PASS dynamic ranking: {', '.join(sorted(top_names))}")
    print("All AirWater AI smoke tests passed.")


if __name__ == "__main__":
    main()
