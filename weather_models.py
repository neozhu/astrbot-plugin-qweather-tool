from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NormalizedCurrentWeather:
    """Normalized QWeather v1 Current weather data (SPEC §10.4)."""
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    condition: Optional[str] = None
    wind_speed_ms: Optional[float] = None
    wind_gust_ms: Optional[float] = None
    precipitation_mm: Optional[float] = None
    relative_humidity: Optional[float] = None
    precipitation_probability: Optional[float] = None  # None for Current (SPEC C49)
    obs_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "current",
            "temperature_c": self.temperature_c,
            "feels_like_c": self.feels_like_c,
            "condition": self.condition,
            "wind_speed_ms": self.wind_speed_ms,
            "wind_gust_ms": self.wind_gust_ms,
            "precipitation_mm": self.precipitation_mm,
            "precipitation_probability": self.precipitation_probability,
            "relative_humidity": self.relative_humidity,
            "obs_time": self.obs_time,
        }


def _parse_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def normalize_current_weather(now_data: Dict[str, Any]) -> NormalizedCurrentWeather:
    """Normalize QWeather v1 Current JSON payload conforming to SPEC-v1.3 §10.4."""
    if not isinstance(now_data, dict):
        return NormalizedCurrentWeather()

    temp_dict = now_data.get("temperature", {})
    temp_val = _parse_float(temp_dict.get("value") if isinstance(temp_dict, dict) else temp_dict)

    feels_dict = now_data.get("feelsLike", {})
    feels_val = _parse_float(feels_dict.get("value") if isinstance(feels_dict, dict) else feels_dict)

    cond_dict = now_data.get("condition", {})
    cond_text = cond_dict.get("text") if isinstance(cond_dict, dict) else now_data.get("text")

    wind_dict = now_data.get("wind", {})
    wind_speed = None
    wind_gust = None
    if isinstance(wind_dict, dict):
        speed_dict = wind_dict.get("speed", {})
        wind_speed = _parse_float(speed_dict.get("value") if isinstance(speed_dict, dict) else speed_dict)
        gust_dict = now_data.get("windGust", {})
        wind_gust = _parse_float(gust_dict.get("value") if isinstance(gust_dict, dict) else gust_dict)

    precip_dict = now_data.get("precipitation", {})
    precip_mm = None
    if isinstance(precip_dict, dict):
        amount_dict = precip_dict.get("amount", {})
        precip_mm = _parse_float(amount_dict.get("value") if isinstance(amount_dict, dict) else amount_dict)

    humidity_raw = now_data.get("humidity")
    humidity = _parse_float(humidity_raw)
    if humidity is not None and humidity > 1.0:
        humidity = round(humidity / 100.0, 2)

    # Precipitation probability is NOT provided in QWeather Current v1 (SPEC §10.4, C49)
    # Must remain None, NEVER 0.
    return NormalizedCurrentWeather(
        temperature_c=temp_val,
        feels_like_c=feels_val,
        condition=str(cond_text) if cond_text else None,
        wind_speed_ms=wind_speed,
        wind_gust_ms=wind_gust,
        precipitation_mm=precip_mm,
        relative_humidity=humidity,
        precipitation_probability=None,
        obs_time=now_data.get("obsTime"),
    )
