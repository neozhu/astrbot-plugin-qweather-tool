import datetime
from typing import Any, Dict, Optional

from location_resolver import LocationResolver, ResolvedLocation
from qweather_client import QWeatherClient
from weather_models import normalize_current_weather


class WeatherService:
    """Weather service orchestrator tying LocationResolver, Client, and QueryPlanning."""

    def __init__(self, api_host: str = "", api_key: str = "", default_city: str = ""):
        self.api_host = (api_host or "").strip()
        self.api_key = (api_key or "").strip()
        self.default_city = (default_city or "").strip()

        if self.api_host:
            self.client = QWeatherClient(api_host=self.api_host, api_key=self.api_key)
        else:
            self.client = None

        self.location_resolver = LocationResolver(self.client)

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

        # 1. Resolve Location (SPEC §6)
        loc_res = await self.location_resolver.resolve(
            location_text=location_text,
            default_city=self.default_city,
            is_group=is_group,
            confirmed_location=confirmed_location,
        )

        if loc_res.status == "need_location":
            return {
                "status": "need_location",
                "warnings": [],
            }

        if loc_res.status == "ambiguous_location":
            return {
                "status": "ambiguous_location",
                "candidates": loc_res.candidates,
                "warnings": ["multiple_matching_locations"],
            }

        loc = loc_res.location
        loc_dict = loc.to_dict() if loc else {}

        raw_when = (when_text or "").strip()
        focus = (focus_text or "general").strip()
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        warnings = []

        # 2. Time resolution handling (SPEC §7.4)
        if not raw_when:
            # Empty / unspecified time defaults to now
            time_status = "fallback_now"
            time_reason = "time_unspecified"
            normalized_when = "now"
        elif raw_when in ["now", "现在", "此刻"]:
            time_status = "explicit"
            time_reason = None
            normalized_when = "now"
        elif any(token in raw_when for token in ["today", "今天", "tomorrow", "明天", "+", "@"]):
            time_status = "explicit"
            time_reason = None
            normalized_when = raw_when
        else:
            # Unrecognized explicit time -> fallback to now (SPEC C59)
            time_status = "fallback_now"
            time_reason = "unrecognized_explicit_time"
            normalized_when = "now"
            warnings.append("time_fallback_now")

        # 3. Query Execution
        # For 'now' queries, strictly fetch Current weather (SPEC §8.1)
        current_snapshot = None
        data_kinds = ["mock"]

        weather_data: Dict[str, Any] = {
            "condition": "多云",
            "temperature_c": 18.0,
            "feels_like_c": 17.5,
            "temperature_min_c": 12.0,
            "temperature_max_c": 21.0,
            "wind_speed_ms": 3.0,
            "precipitation_probability": None,
            "precipitation_mm": 0.0,
            "relevant_hours": [],
        }

        if normalized_when == "now" and loc:
            data_kinds = ["current"]
            if self.client:
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
                "normalized_when": normalized_when,
                "time_resolution": {
                    "status": time_status,
                    "reason": time_reason,
                },
                "target_start_local": "2026-11-04T00:00:00+08:00",
                "target_end_local": "2026-11-05T00:00:00+08:00",
                "granularity": "current" if normalized_when == "now" else "daily",
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
