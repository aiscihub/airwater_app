from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests


DEMO_LOCATIONS: Dict[str, Tuple[float, float, str]] = {
    "Phoenix, Arizona": (33.4484, -112.0740, "hot_dry"),
    "Mojave Desert, California": (35.0110, -115.4734, "hot_dry"),
    "Miami, Florida": (25.7617, -80.1918, "warm_humid"),
    "Singapore": (1.3521, 103.8198, "tropical"),
    "Nairobi, Kenya": (-1.2864, 36.8172, "mild_seasonal"),
}

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def geocode_location(query: str, limit: int = 5) -> List[Dict[str, object]]:
    """Resolve a free-text place name to candidate coordinates.

    Uses the free Open-Meteo geocoding API (no key required). Raises on
    network failure so the caller can surface a clear "offline" message.
    """
    query = query.strip()
    if len(query) < 2:
        return []
    response = requests.get(
        GEOCODE_URL,
        params={"name": query, "count": limit, "language": "en", "format": "json"},
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    results = []
    for item in payload.get("results", []) or []:
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        if latitude is None or longitude is None:
            continue
        parts = [item.get("name"), item.get("admin1"), item.get("country")]
        display_name = ", ".join(part for part in parts if part)
        results.append(
            {
                "display_name": display_name or item.get("name"),
                "latitude": float(latitude),
                "longitude": float(longitude),
                "country": item.get("country"),
                "admin1": item.get("admin1"),
                "climate_kind": climate_kind_from_coordinates(float(latitude), float(longitude)),
            }
        )
    return results


def climate_kind_from_coordinates(latitude: float, longitude: float) -> str:
    """Coarse climate-band guess for the offline synthetic fallback curve.

    This is a latitude-band heuristic, not a climate classification model.
    It only decides which demo curve shape to fall back to if NASA POWER
    is unreachable for a searched (non-preset) location.
    """
    band = abs(latitude)
    if band <= 15:
        return "tropical"
    if band <= 30:
        return "hot_dry"
    if band <= 45:
        return "warm_humid"
    return "mild_seasonal"


@dataclass(frozen=True)
class ClimateRequest:
    location_name: str
    latitude: float
    longitude: float
    month: int = 7
    use_live_nasa: bool = False
    climate_kind: str = "generic"
    specific_date: Optional[date] = None


def _solar_curve(hours: np.ndarray, sunrise: float, sunset: float, peak: float) -> np.ndarray:
    daylight = (hours >= sunrise) & (hours <= sunset)
    solar = np.zeros_like(hours, dtype=float)
    phase = (hours[daylight] - sunrise) / max(sunset - sunrise, 1.0)
    solar[daylight] = peak * np.sin(np.pi * phase)
    return np.maximum(solar, 0)


def synthetic_profile(kind: str, month: int = 7) -> pd.DataFrame:
    """Return a 24-hour demo climate profile.

    The shape is designed for product demonstration. It is not a forecast.
    Month shifts temperature, humidity, day length, and solar intensity so
    that, e.g., January and August visibly differ for the same location.
    """
    hours = np.arange(24)
    season = np.cos((month - 7) / 12 * 2 * np.pi)  # +1 in July, -1 in January
    seasonal_shift = 2.5 * season
    rh_seasonal = -5.0 * season
    daylight_shift = 1.1 * season
    solar_seasonal = 0.82 + 0.18 * season

    def solar(sunrise: float, sunset: float, peak: float) -> np.ndarray:
        return _solar_curve(hours, sunrise - daylight_shift, sunset + daylight_shift, peak * solar_seasonal)

    if kind == "hot_dry":
        temp = 34 + seasonal_shift + 9 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = 24 + rh_seasonal - 10 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = np.clip(rh, 8, 45)
        solar_w = solar(6, 19, 930)
    elif kind == "warm_humid":
        temp = 28 + seasonal_shift + 3 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = 72 + rh_seasonal - 15 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = np.clip(rh, 52, 94)
        solar_w = solar(6, 20, 720)
    elif kind == "tropical":
        temp = 29 + seasonal_shift + 2 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = 80 + rh_seasonal - 10 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = np.clip(rh, 62, 95)
        solar_w = solar(6, 19, 650)
    elif kind == "mild_seasonal":
        temp = 22 + seasonal_shift + 6 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = 55 + rh_seasonal - 18 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = np.clip(rh, 28, 88)
        solar_w = solar(6.5, 18.5, 760)
    else:
        temp = 26 + seasonal_shift + 5 * np.sin((hours - 8) / 24 * 2 * np.pi)
        rh = 50 + rh_seasonal - 12 * np.sin((hours - 8) / 24 * 2 * np.pi)
        solar_w = solar(6, 19, 700)

    df = pd.DataFrame(
        {
            "hour": hours,
            "temperature_c": np.round(temp, 1),
            "relative_humidity_percent": np.round(rh, 1),
            "solar_w_m2": np.round(solar_w, 0),
        }
    )
    return df


def fetch_nasa_power_hourly(latitude: float, longitude: float, day: date) -> pd.DataFrame:
    """Fetch one day of hourly meteorology from NASA POWER.

    NASA POWER parameter names can change over time. The function is intentionally
    defensive and raises a helpful exception so the app can fall back to demo data.
    """
    start = day.strftime("%Y%m%d")
    end = start
    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    params = {
        "parameters": "T2M,RH2M,ALLSKY_SFC_SW_DWN",
        "community": "RE",
        "longitude": longitude,
        "latitude": latitude,
        "start": start,
        "end": end,
        "format": "JSON",
    }
    response = requests.get(url, params=params, timeout=12)
    response.raise_for_status()
    payload = response.json()
    parameters = payload.get("properties", {}).get("parameter", {})
    if not parameters:
        raise RuntimeError("NASA POWER response did not contain hourly parameters.")

    # NASA POWER fills unprocessed hours with a -999 sentinel instead of omitting
    # them. Its near-real-time source (T2M, RH2M) usually lags ~1-2 days, but its
    # satellite-derived solar source (ALLSKY_SFC_SW_DWN) can lag ~3 months - so for
    # any "recent" date, solar is fill-valued far more often than temperature or
    # humidity are. Treat temperature/humidity fill values as a hard failure (the
    # whole day falls back to the synthetic profile), but leave a fill-valued solar
    # column as NaN for the caller to patch, so a genuinely available day of real
    # temperature/humidity isn't thrown away just because solar isn't ready yet.
    FILL_THRESHOLD = -900.0

    rows = []
    for timestamp, temp_c in parameters.get("T2M", {}).items():
        hour = int(timestamp[-2:])
        rh = parameters.get("RH2M", {}).get(timestamp)
        solar = parameters.get("ALLSKY_SFC_SW_DWN", {}).get(timestamp)
        temp_c, rh, solar = float(temp_c), float(rh), float(solar)
        if temp_c < FILL_THRESHOLD or rh < FILL_THRESHOLD:
            raise RuntimeError(
                f"NASA POWER has not processed temperature/humidity data for {day.isoformat()} "
                "yet (it typically lags 1-2 days behind today)."
            )
        rows.append(
            {
                "hour": hour,
                "temperature_c": temp_c,
                "relative_humidity_percent": rh,
                "solar_w_m2": np.nan if solar < FILL_THRESHOLD else max(solar, 0.0),
            }
        )
    if not rows:
        raise RuntimeError("NASA POWER response contained no hourly rows.")
    return pd.DataFrame(rows).sort_values("hour").reset_index(drop=True)


def get_climate_profile(request: ClimateRequest) -> Tuple[pd.DataFrame, str]:
    if request.use_live_nasa:
        # NASA POWER is a historical record, not a forecast, so a specific date falls
        # back to the 15th of the requested month as a representative sample day.
        if request.specific_date is not None:
            day = request.specific_date
        else:
            today = date.today()
            year = min(today.year - 1, 2025)
            day = date(year, request.month, 15)
        try:
            df = fetch_nasa_power_hourly(request.latitude, request.longitude, day)
            source = "NASA POWER hourly historical sample"
            if df["solar_w_m2"].isna().any():
                # Solar hasn't finished processing for this date yet (its satellite
                # source lags much longer than temperature/humidity). Prefer real
                # solar from the same calendar date a year back - same season, actual
                # measured data - and only fall back to a modeled curve if that
                # secondary fetch also comes back incomplete.
                prior_year_day = date.fromordinal(day.toordinal() - 365)
                try:
                    prior_df = fetch_nasa_power_hourly(request.latitude, request.longitude, prior_year_day)
                    if prior_df["solar_w_m2"].isna().any():
                        raise RuntimeError("prior-year solar also incomplete")
                    df["solar_w_m2"] = df["solar_w_m2"].fillna(
                        pd.Series(prior_df["solar_w_m2"].values, index=df.index)
                    )
                    source = (
                        "NASA POWER hourly (temperature/humidity live; solar from "
                        f"{prior_year_day.isoformat()}, same date a year prior - satellite solar data lags longer)"
                    )
                except Exception:
                    modeled_solar = synthetic_profile(request.climate_kind, request.month)["solar_w_m2"]
                    df["solar_w_m2"] = df["solar_w_m2"].fillna(pd.Series(modeled_solar.values, index=df.index))
                    source = "NASA POWER hourly (temperature/humidity live; solar modeled - satellite solar data lags longer)"
            return df, source
        except Exception as exc:
            df = synthetic_profile(request.climate_kind, request.month)
            return df, f"Demo profile fallback; NASA fetch failed: {exc}"

    return synthetic_profile(request.climate_kind, request.month), "Offline demo climate profile"
