from __future__ import annotations

import argparse
import calendar
import json
import mimetypes
import os
import re
import smtplib
import ssl
import threading
import time
import webbrowser
from datetime import date, datetime, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

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
FEEDBACK_FILE = ROOT / "data" / "feedback_submissions.jsonl"
FEEDBACK_MAX_REQUEST_BYTES = 16 * 1024
FEEDBACK_WEBHOOK_ENV = "AIRWATER_FEEDBACK_WEBHOOK_URL"
FEEDBACK_SMTP_HOST_ENV = "AIRWATER_FEEDBACK_SMTP_HOST"
FEEDBACK_SMTP_PORT_ENV = "AIRWATER_FEEDBACK_SMTP_PORT"
FEEDBACK_SMTP_USERNAME_ENV = "AIRWATER_FEEDBACK_SMTP_USERNAME"
FEEDBACK_SMTP_PASSWORD_ENV = "AIRWATER_FEEDBACK_SMTP_PASSWORD"
FEEDBACK_SMTP_USE_SSL_ENV = "AIRWATER_FEEDBACK_SMTP_USE_SSL"
FEEDBACK_EMAIL_FROM_ENV = "AIRWATER_FEEDBACK_EMAIL_FROM"
FEEDBACK_EMAIL_TO_ENV = "AIRWATER_FEEDBACK_EMAIL_TO"
FEEDBACK_EMAIL_TO_DEFAULT = "aiscihub@gmail.com"
_FEEDBACK_FILE_LOCK = threading.Lock()
_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class FeedbackDeliveryError(RuntimeError):
    """Raised when a valid feedback submission cannot be delivered."""


class RequestTooLargeError(ValueError):
    """Raised when an HTTP request body exceeds its endpoint limit."""


def _normalize_feedback(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    category_value = payload.get("category")
    if not isinstance(category_value, str):
        raise ValueError("Category must be either 'question' or 'feedback'.")
    category = category_value.strip().lower()
    if category not in {"question", "feedback"}:
        raise ValueError("Category must be either 'question' or 'feedback'.")

    email_value = payload.get("email")
    if not isinstance(email_value, str):
        raise ValueError("A valid email address is required.")
    email = email_value.strip()
    if len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("A valid email address of at most 254 characters is required.")
    local_part, domain = email.rsplit("@", 1)
    if len(local_part) > 64:
        raise ValueError("A valid email address of at most 254 characters is required.")
    email = f"{local_part}@{domain.lower()}"

    message_value = payload.get("message")
    if not isinstance(message_value, str):
        raise ValueError("Message must be between 5 and 2000 characters.")
    message = message_value.strip()
    if not 5 <= len(message) <= 2000:
        raise ValueError("Message must be between 5 and 2000 characters.")

    honeypot = payload.get("website", "")
    if not isinstance(honeypot, str) or honeypot.strip():
        raise ValueError("Invalid submission.")

    page_value = payload.get("page")
    if page_value is None:
        page = None
    elif isinstance(page_value, str):
        page = page_value.strip() or None
        if page is not None and len(page) > 2048:
            raise ValueError("Page must be at most 2048 characters.")
    else:
        raise ValueError("Page must be a string when provided.")

    return {
        "category": category,
        "email": email,
        "message": message,
        "page": page,
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _append_feedback(record: dict[str, Any], destination: Path = FEEDBACK_FILE) -> None:
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with _FEEDBACK_FILE_LOCK:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as feedback_file:
            feedback_file.write(line)
            feedback_file.flush()


def _post_feedback_webhook(record: dict[str, Any], webhook_url: str) -> None:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FeedbackDeliveryError(
            f"{FEEDBACK_WEBHOOK_ENV} must be a valid HTTPS URL without embedded credentials."
        )

    body = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "AirWaterAI/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
            if not 200 <= status < 300:
                raise FeedbackDeliveryError(f"Feedback webhook returned HTTP {status}.")
    except HTTPError as exc:
        raise FeedbackDeliveryError(f"Feedback webhook returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FeedbackDeliveryError(f"Feedback webhook could not be reached: {reason}") from exc


def _send_feedback_email(record: dict[str, Any], smtp_host: str) -> None:
    port_raw = os.environ.get(FEEDBACK_SMTP_PORT_ENV, "587").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise FeedbackDeliveryError(f"{FEEDBACK_SMTP_PORT_ENV} must be an integer port number.") from exc

    username = os.environ.get(FEEDBACK_SMTP_USERNAME_ENV, "").strip()
    password = os.environ.get(FEEDBACK_SMTP_PASSWORD_ENV, "")
    sender = os.environ.get(FEEDBACK_EMAIL_FROM_ENV, "").strip() or username
    recipient = os.environ.get(FEEDBACK_EMAIL_TO_ENV, "").strip() or FEEDBACK_EMAIL_TO_DEFAULT
    use_ssl = os.environ.get(FEEDBACK_SMTP_USE_SSL_ENV, "").strip().lower() in {"1", "true", "yes"}

    if not sender:
        raise FeedbackDeliveryError(
            f"{FEEDBACK_EMAIL_FROM_ENV} or {FEEDBACK_SMTP_USERNAME_ENV} must be set to send feedback email."
        )

    message = EmailMessage()
    message["Subject"] = f"AirWater {record['category']} from {record['email']}"
    message["From"] = sender
    message["To"] = recipient
    message["Reply-To"] = record["email"]
    message.set_content(
        "Category: {category}\n"
        "From: {email}\n"
        "Page: {page}\n"
        "Submitted: {submitted_at}\n\n"
        "{message}\n".format(**record)
    )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, port, timeout=10, context=ssl.create_default_context()) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, port, timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise FeedbackDeliveryError(f"Feedback email could not be sent: {exc}") from exc


def _deliver_feedback(record: dict[str, Any]) -> None:
    smtp_host = os.environ.get(FEEDBACK_SMTP_HOST_ENV, "").strip()
    webhook_url = os.environ.get(FEEDBACK_WEBHOOK_ENV, "").strip()
    if smtp_host:
        _send_feedback_email(record, smtp_host)
    elif webhook_url:
        _post_feedback_webhook(record, webhook_url)
    else:
        _append_feedback(record)


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

    def _read_json_body(self, max_bytes: int) -> Any:
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json.")

        raw_content_length = self.headers.get("Content-Length")
        try:
            content_length = int(raw_content_length or "0")
        except ValueError as exc:
            raise ValueError("Content-Length must be a valid integer.") from exc
        if content_length <= 0:
            raise ValueError("Request body is required.")
        if content_length > max_bytes:
            raise RequestTooLargeError(f"Request body must not exceed {max_bytes} bytes.")

        try:
            return json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must contain valid UTF-8 JSON.") from exc

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
        if parsed.path == "/api/feedback":
            try:
                payload = self._read_json_body(FEEDBACK_MAX_REQUEST_BYTES)
                record = _normalize_feedback(payload)
                _deliver_feedback(record)
                self._send_json(
                    {"ok": True, "message": "Thanks. Your message has been received."},
                    HTTPStatus.CREATED,
                )
            except RequestTooLargeError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except FeedbackDeliveryError as exc:
                self._send_json(
                    {"ok": False, "error": "Feedback delivery failed.", "detail": str(exc)},
                    HTTPStatus.BAD_GATEWAY,
                )
            except Exception:
                self._send_json(
                    {"ok": False, "error": "Feedback could not be saved."},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

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
