from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import analyze  # noqa: E402


def main() -> None:
    payload = {
        "location": "Phoenix, Arizona",
        "month": 7,
        "mass_kg": 10,
        "target_liters_day": 3,
        "max_regen_temp_c": 85,
        "energy_source": "Solar only",
        "efficiency": 0.55,
        "data_source": "Offline demo profile",
    }

    result = analyze(payload)

    assert "decision" in result
    assert result["decision"] in {"VIABLE", "DO NOT DEPLOY"}
    assert "material" in result
    assert "climate_archetype" in result
    assert "yield_lpd" in result
    assert "energy_kwh_per_l" in result
    assert "cost_per_l" in result
    assert "yield_90_ci" in result
    assert "alternative_materials" in result
    assert "explanation" in result
    assert "schedule" in result
    assert "recommended_schedule" in result
    assert isinstance(result["explanation"], list)
    assert isinstance(result["alternative_materials"], list)
    assert isinstance(result["yield_90_ci"], list)
    assert len(result["yield_90_ci"]) == 2
    print("AI spec regression checks passed.")


if __name__ == "__main__":
    main()
