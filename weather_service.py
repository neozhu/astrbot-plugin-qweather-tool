import datetime
from typing import Any, Dict, List, Optional, Tuple

from location_resolver import LocationResolver, ResolvedLocation
from qweather_client import QWeatherClient
from time_resolver import TimeResolver
from weather_models import (
    normalize_current_weather,
    normalize_daily_forecast,
    normalize_hourly_forecast,
)

FOCUS_ALIASES = {
    "general": ["general", "综合", "综合天气", "概况", "天气"],
    "rain": ["rain", "下雨", "降雨", "带伞", "雨"],
    "clothing": ["clothing", "穿衣", "加衣", "保暖", "体感"],
    "comparison": ["comparison", "对比", "温差", "比较"],
    "outdoor": ["outdoor", "户外", "跑步", "骑车", "运动", "出门"],
    "car_wash": ["car_wash", "洗车", "晾晒"],
}


def normalize_focus(focus_text: str) -> Tuple[str, Optional[str]]:
    """Normalize user/LLM focus parameter to controlled enum (SPEC §4.1, §8.2)."""
    f = (focus_text or "").strip().lower()
    if not f:
        return "general", None

    for canonical, aliases in FOCUS_ALIASES.items():
        if f == canonical or any(a in f for a in aliases):
            return canonical, None

    # Unrecognized focus falls back to general (SPEC §8.2, C61)
    return "general", "focus_fallback_general"


class WeatherService:
    """Weather service orchestrator tying LocationResolver, TimeResolver, and QueryPlanning."""

    def __init__(self, api_host: str = "", api_key: str = "", default_city: str = ""):
        self.api_host = (api_host or "").strip()
        self.api_key = (api_key or "").strip()
        self.default_city = (default_city or "").strip()

        if self.api_host:
            self.client = QWeatherClient(api_host=self.api_host, api_key=self.api_key)
        else:
            self.client = None

        self.location_resolver = LocationResolver(self.client)
        self.time_resolver = TimeResolver()

    def _is_group_event(self, event: Any) -> bool:
        """Detect whether the current message origin is a group chat."""
        if hasattr(event, "message_obj"):
            msg_obj = getattr(event, "message_obj")
            if getattr(msg_obj, "group_id", None) or getattr(msg_obj, "is_group", False):
                return True
        origin = getattr(event, "unified_msg_origin", "")
        if "group" in str(origin).lower():
            return True
        return False

    async def query(
        self,
        event: Any,
        location_text: str,
        when_text: str,
        focus_text: str,
        confirmed_location: Optional[ResolvedLocation] = None,
    ) -> Dict[str, Any]:
        """Process weather query and return structured Tool Result."""
        is_group = self._is_group_event(event)
        warnings: List[str] = []

        # 1. Normalize Focus (SPEC §4.1, §8.2)
        focus, focus_warning = normalize_focus(focus_text)
        if focus_warning:
            warnings.append(focus_warning)

        # 2. Resolve Location (SPEC §6)
        loc_res = await self.location_resolver.resolve(
            location_text=location_text,
            default_city=self.default_city,
            is_group=is_group,
            confirmed_location=confirmed_location,
        )

        if loc_res.status == "need_location":
            return {
                "status": "need_location",
                "warnings": warnings,
            }

        if loc_res.status == "ambiguous_location":
            warnings.append("multiple_matching_locations")
            return {
                "status": "ambiguous_location",
                "candidates": loc_res.candidates,
                "warnings": warnings,
            }

        loc = loc_res.location
        loc_dict = loc.to_dict() if loc else {}
        loc_tz = loc.tz if loc else "Asia/Shanghai"

        # 3. Resolve Time (SPEC §7)
        time_res = self.time_resolver.resolve(when_text=when_text, tz=loc_tz)
        target = time_res.target
        resolution = time_res.resolution

        if resolution.status == "fallback_now" and resolution.reason == "unrecognized_explicit_time":
            warnings.append("time_fallback_now")

        # 4. Query Planning (SPEC §8)
        current_snapshot = None
        data_kinds: List[str] = []
        weather_data: Dict[str, Any] = {
            "condition": None,
            "temperature_c": None,
            "feels_like_c": None,
            "temperature_min_c": None,
            "temperature_max_c": None,
            "wind_speed_ms": None,
            "precipitation_probability": None,
            "precipitation_mm": None,
            "relevant_hours": [],
        }

        # Case A: Instant (now / fallback_now)
        if target.granularity == "instant":
            data_kinds = ["current"]
            if self.client and loc:
                raw_current = await self.client.get_current_weather(lat=loc.lat, lon=loc.lon)
                if raw_current:
                    norm = normalize_current_weather(raw_current)
                    current_snapshot = norm.to_dict()
                    weather_data = {
                        "condition": norm.condition,
                        "temperature_c": norm.temperature_c,
                        "feels_like_c": norm.feels_like_c,
                        "temperature_min_c": None,
                        "temperature_max_c": None,
                        "wind_speed_ms": norm.wind_speed_ms,
                        "precipitation_probability": None,  # NEVER 0 for current
                        "precipitation_mm": norm.precipitation_mm,
                        "relevant_hours": [],
                    }

        # Case B: Daily (today / tomorrow / +Nd)
        elif target.granularity == "daily":
            data_kinds = ["forecast_daily"]
            if self.client and loc:
                raw_daily = await self.client.get_daily_forecast(lat=loc.lat, lon=loc.lon, days=7)
                norm_daily = normalize_daily_forecast(raw_daily)

                target_date_str = target.start_local[:10]
                matching_day = next((d for d in norm_daily if d.date == target_date_str), None)
                if matching_day:
                    weather_data = {
                        "condition": matching_day.condition_day or matching_day.condition_night,
                        "temperature_c": None,
                        "feels_like_c": None,
                        "temperature_min_c": matching_day.temperature_min_c,
                        "temperature_max_c": matching_day.temperature_max_c,
                        "wind_speed_ms": None,
                        "precipitation_probability": None,
                        "precipitation_mm": matching_day.precipitation_mm,
                        "relevant_hours": [],
                    }

        # Case C: Hourly (dayparts / specific hours / intervals)
        elif target.granularity == "hourly":
            data_kinds = ["forecast_hourly"]
            if self.client and loc:
                raw_hourly = await self.client.get_hourly_forecast(lat=loc.lat, lon=loc.lon, hours=24)
                norm_hourly = normalize_hourly_forecast(raw_hourly, tz_name=loc_tz)

                # Filter hours in [target.start_local, target.end_local)
                relevant = [
                    h for h in norm_hourly
                    if target.start_local <= h.forecast_time_local < target.end_local
                    or (target.start_local[:13] in h.forecast_time_local)
                ]

                if relevant:
                    first = relevant[0]
                    # Compute mean or first
                    weather_data = {
                        "condition": first.condition,
                        "temperature_c": first.temperature_c,
                        "feels_like_c": first.feels_like_c,
                        "temperature_min_c": min((h.temperature_c for h in relevant if h.temperature_c is not None), default=None),
                        "temperature_max_c": max((h.temperature_c for h in relevant if h.temperature_c is not None), default=None),
                        "wind_speed_ms": first.wind_speed_ms,
                        "precipitation_probability": first.precipitation_probability,
                        "precipitation_mm": sum((h.precipitation_mm for h in relevant if h.precipitation_mm is not None), 0.0),
                        "relevant_hours": [h.to_dict() for h in relevant],
                    }

        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return {
            "status": "ok",
            "source": {
                "data_kinds": data_kinds,
                "retrieved_at_utc": now_utc,
            },
            "location": loc_dict,
            "location_resolution": {
                "method": loc_res.resolution_method,
            },
            "query": {
                "focus": focus,
                "normalized_when": target.semantic or (when_text or "now"),
                "time_resolution": resolution.to_dict(),
                "target_start_local": target.start_local,
                "target_end_local": target.end_local,
                "granularity": target.granularity,
            },
            "weather": weather_data,
            "current_snapshot": current_snapshot,
            "comparison": {
                "available": False,
                "reason": "insufficient_comparable_data",
            },
            "warnings": warnings,
            "from_cache": False,
        }

    async def close(self) -> None:
        """Release underlying HTTP client / connections."""
        if self.client:
            await self.client.close()
