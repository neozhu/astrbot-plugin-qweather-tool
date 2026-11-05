from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
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


@dataclass
class NormalizedDailyForecast:
    """Normalized QWeather v1 Daily forecast data (SPEC §10.4)."""
    date: str
    temperature_max_c: Optional[float] = None
    temperature_min_c: Optional[float] = None
    condition_day: Optional[str] = None
    condition_night: Optional[str] = None
    precipitation_mm: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "temperature_max_c": self.temperature_max_c,
            "temperature_min_c": self.temperature_min_c,
            "condition_day": self.condition_day,
            "condition_night": self.condition_night,
            "precipitation_mm": self.precipitation_mm,
        }


@dataclass
class NormalizedHourlyForecast:
    """Normalized QWeather v1 Hourly forecast data (SPEC §10.4)."""
    forecast_time_local: str
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    condition: Optional[str] = None
    wind_speed_ms: Optional[float] = None
    precipitation_probability: Optional[float] = None  # 0..1 ratio (SPEC C34)
    precipitation_mm: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "forecast_time_local": self.forecast_time_local,
            "temperature_c": self.temperature_c,
            "feels_like_c": self.feels_like_c,
            "condition": self.condition,
            "wind_speed_ms": self.wind_speed_ms,
            "precipitation_probability": self.precipitation_probability,
            "precipitation_mm": self.precipitation_mm,
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


def normalize_daily_forecast(daily_list: List[Dict[str, Any]]) -> List[NormalizedDailyForecast]:
    """Normalize QWeather v1 Daily forecast items conforming to SPEC-v1.3 §10.4."""
    results = []
    for d in daily_list:
        max_dict = d.get("temperatureMax", {})
        min_dict = d.get("temperatureMin", {})
        max_val = _parse_float(max_dict.get("value") if isinstance(max_dict, dict) else max_dict)
        min_val = _parse_float(min_dict.get("value") if isinstance(min_dict, dict) else min_dict)

        day_dict = d.get("daytime", {})
        night_dict = d.get("nighttime", {})
        day_cond = day_dict.get("condition", {}).get("text") if isinstance(day_dict, dict) else None
        night_cond = night_dict.get("condition", {}).get("text") if isinstance(night_dict, dict) else None

        precip_dict = d.get("precipitation", {})
        precip_mm = None
        if isinstance(precip_dict, dict):
            amount_dict = precip_dict.get("amount", {})
            precip_mm = _parse_float(amount_dict.get("value") if isinstance(amount_dict, dict) else amount_dict)

        raw_date = d.get("fxDate") or str(d.get("forecastStartTime", ""))[:10]
        results.append(
            NormalizedDailyForecast(
                date=str(raw_date),
                temperature_max_c=max_val,
                temperature_min_c=min_val,
                condition_day=day_cond,
                condition_night=night_cond,
                precipitation_mm=precip_mm,
            )
        )
    return results


def normalize_hourly_forecast(
    hourly_list: List[Dict[str, Any]],
    tz_name: str = "Asia/Shanghai",
) -> List[NormalizedHourlyForecast]:
    """Normalize QWeather v1 Hourly forecast items conforming to SPEC-v1.3 §10.4, C34."""
    results = []
    for h in hourly_list:
        temp_dict = h.get("temperature", {})
        temp_val = _parse_float(temp_dict.get("value") if isinstance(temp_dict, dict) else temp_dict)

        feels_dict = h.get("feelsLike", {})
        feels_val = _parse_float(feels_dict.get("value") if isinstance(feels_dict, dict) else feels_dict)

        cond_dict = h.get("condition", {})
        cond_text = cond_dict.get("text") if isinstance(cond_dict, dict) else h.get("text")

        wind_dict = h.get("wind", {})
        wind_speed = None
        if isinstance(wind_dict, dict):
            speed_dict = wind_dict.get("speed", {})
            wind_speed = _parse_float(speed_dict.get("value") if isinstance(speed_dict, dict) else speed_dict)

        precip_dict = h.get("precipitation", {})
        precip_mm = None
        precip_prob = None
        if isinstance(precip_dict, dict):
            amount_dict = precip_dict.get("amount", {})
            precip_mm = _parse_float(amount_dict.get("value") if isinstance(amount_dict, dict) else amount_dict)

            prob_val = _parse_float(precip_dict.get("probability"))
            if prob_val is not None:
                # If returned as 0-100 percentage, convert to 0..1 ratio (SPEC C34)
                if prob_val > 1.0:
                    prob_val = round(prob_val / 100.0, 2)
                precip_prob = prob_val

        forecast_time = str(h.get("forecastTime", ""))
        try:
            parsed_time = datetime.fromisoformat(forecast_time.replace("Z", "+00:00"))
            local_tz = ZoneInfo(tz_name)
            if parsed_time.tzinfo is None:
                parsed_time = parsed_time.replace(tzinfo=local_tz)
            forecast_time = parsed_time.astimezone(local_tz).isoformat()
        except ValueError:
            pass

        results.append(
            NormalizedHourlyForecast(
                forecast_time_local=forecast_time,
                temperature_c=temp_val,
                feels_like_c=feels_val,
                condition=cond_text,
                wind_speed_ms=wind_speed,
                precipitation_probability=precip_prob,
                precipitation_mm=precip_mm,
            )
        )
    return results
