from __future__ import annotations

import argparse
import calendar
import json
import mimetypes
import threading
import time
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

from airwater.climate import (
    DEMO_LOCATIONS,
    ClimateRequest,
    climate_kind_from_coordinates,
    geocode_location,
    get_climate_profile,
)
from airwater.decision import build_ai_decision
from airwater.selector import get_isotherm_detail, list_materials, load_feature_importance, load_metrics, rank_mofs

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records"))


def _resolve_location(payload: dict[str, Any]) -> dict[str, Any]:
    location = str(payload.get("location", "Phoenix, Arizona"))
    if location in DEMO_LOCATIONS:
        latitude, longitude, climate_kind = DEMO_LOCATIONS[location]
        return {
            "location": location,
            "latitude": latitude,
            "longitude": longitude,
            "climate_kind": climate_kind,
        }

    # Custom (geocoded) location: the client must supply the coordinates it
    # already resolved via /api/geocode. Coordinates are validated here rather
    # than trusted blindly.
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError("Unknown location. Search for a location or choose a preset.")
    latitude = float(latitude)
    longitude = float(longitude)
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("Location coordinates are out of range.")
    climate_kind = str(payload.get("climate_kind") or climate_kind_from_coordinates(latitude, longitude))
    return {
        "location": location,
        "latitude": latitude,
        "longitude": longitude,
        "climate_kind": climate_kind,
    }


def _scenario(payload: dict[str, Any]) -> dict[str, Any]:
    resolved_location = _resolve_location(payload)

    month = int(payload.get("month", 7))
    if month < 1 or month > 12:
        raise ValueError("Month must be between 1 and 12.")

    specific_date: date | None = None
    raw_date = payload.get("date")
    if raw_date:
        try:
            specific_date = date.fromisoformat(str(raw_date))
        except ValueError as exc:
            raise ValueError("Date must be in YYYY-MM-DD format.") from exc
        if specific_date > date.today():
            raise ValueError("Date cannot be in the future.")
        month = specific_date.month

    mass_kg = float(payload.get("mass_kg", 10.0))
    target_liters_day = float(payload.get("target_liters_day", 3.0))
    max_regen_temp_c = float(payload.get("max_regen_temp_c", 85.0))
    efficiency = float(payload.get("efficiency", 0.55))
    energy_source = str(payload.get("energy_source", "Solar only"))
    data_source = str(payload.get("data_source", "NASA POWER historical sample"))
    alternative_cost_per_l = float(payload.get("alternative_cost_per_l", 0.50))

    if not 0.5 <= mass_kg <= 100.0:
        raise ValueError("MOF mass is outside the demo range.")
    if not 0.1 <= target_liters_day <= 100.0:
        raise ValueError("Water target is outside the demo range.")
    if not 40.0 <= max_regen_temp_c <= 150.0:
        raise ValueError("Regeneration temperature is outside the demo range.")
    if not 0.05 <= efficiency <= 0.95:
        raise ValueError("Efficiency must be between 0.05 and 0.95.")
    if energy_source not in {"Solar only", "Electricity or hybrid", "Waste heat"}:
        raise ValueError("Unknown energy source.")
    if not 0.01 <= alternative_cost_per_l <= 5.0:
        raise ValueError("Alternative water cost is outside the demo range.")

    return {
        **resolved_location,
        "month": month,
        "date": specific_date.isoformat() if specific_date else None,
        "mass_kg": mass_kg,
        "target_liters_day": target_liters_day,
        "max_regen_temp_c": max_regen_temp_c,
        "efficiency": efficiency,
        "energy_source": energy_source,
        "data_source": data_source,
        "alternative_cost_per_l": alternative_cost_per_l,
    }


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    scenario = _scenario(payload)
    request = ClimateRequest(
        location_name=scenario["location"],
        latitude=scenario["latitude"],
        longitude=scenario["longitude"],
        month=scenario["month"],
        use_live_nasa=scenario["data_source"] == "NASA POWER historical sample",
        climate_kind=scenario["climate_kind"],
        specific_date=date.fromisoformat(scenario["date"]) if scenario["date"] else None,
    )
    climate, climate_source = get_climate_profile(request)
    ranking, schedule = rank_mofs(
        climate=climate,
        mass_kg=scenario["mass_kg"],
        max_regen_temp_c=scenario["max_regen_temp_c"],
        target_liters_day=scenario["target_liters_day"],
        energy_source=scenario["energy_source"],
        efficiency=scenario["efficiency"],
    )
    metrics = load_metrics()
    importance = load_feature_importance()

    capture_hours = schedule.loc[schedule["action"] == "Capture", "hour"].astype(int).tolist()
    release_hours = schedule.loc[schedule["action"] == "Release + condense", "hour"].astype(int).tolist()
    top = ranking.iloc[0]

    ai_decision = build_ai_decision(
        climate=climate,
        ranking=ranking,
        schedule=schedule,
        mass_kg=scenario["mass_kg"],
        target_liters_day=scenario["target_liters_day"],
        energy_source=scenario["energy_source"],
        alternative_cost_per_l=scenario["alternative_cost_per_l"],
    )

    return {
        **ai_decision,
        "scenario": {
            **scenario,
            "month_name": calendar.month_name[scenario["month"]],
        },
        "climate_source": climate_source,
        "climate": _records(climate),
        "schedule": _records(schedule),
        "ranking": _records(ranking),
        "top": json.loads(top.to_json()),
        "climate_summary": {
            "average_humidity_percent": round(float(climate["relative_humidity_percent"].mean()), 1),
            "average_temperature_c": round(float(climate["temperature_c"].mean()), 1),
            "peak_solar_w_m2": round(float(climate["solar_w_m2"].max()), 0),
            "capture_hours": capture_hours,
            "release_hours": release_hours,
        },
        "metrics": metrics,
        "feature_importance": _records(importance),
        "disclaimer": (
            "Research-screening estimate only. Laboratory adsorption tests, cycling tests, "
            "leaching checks, device engineering, and water-quality verification are required."
        ),
        "generated_at_unix": int(time.time()),
    }


class AirWaterHandler(BaseHTTPRequestHandler):
    server_version = "AirWaterAI/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        mime_type, _ = mimetypes.guess_type(path.name)
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if path.name.endswith(".html"):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self._send_json({"status": "ok", "app": "AirWater AI"})
            return
        if path == "/api/locations":
            locations = [
                {
                    "name": name,
                    "latitude": values[0],
                    "longitude": values[1],
                    "climate_kind": values[2],
                }
                for name, values in DEMO_LOCATIONS.items()
            ]
            self._send_json({"locations": locations})
            return
        if path == "/api/materials":
            self._send_json({"materials": _records(list_materials())})
            return
        if path == "/api/isotherm":
            material = (parse_qs(parsed.query).get("material") or [""])[0]
            try:
                self._send_json(get_isotherm_detail(material))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/geocode":
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            try:
                results = geocode_location(query)
                self._send_json({"results": results})
            except Exception as exc:
                self._send_json({"results": [], "error": f"Geocoding unavailable: {exc}"})
            return

        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        requested = (WEB_ROOT / relative).resolve()
        try:
            requested.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid path")
            return
        self._send_file(requested)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/analyze":
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 1_000_000:
                raise ValueError("Invalid request size.")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            result = analyze(payload)
            self._send_json(result)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json(
                {"error": "Analysis failed.", "detail": f"{type(exc).__name__}: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AirWater AI local demo application.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AirWaterHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"AirWater AI is running at {url}")
    print("Press Ctrl+C to stop the server.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AirWater AI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
