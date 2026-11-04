import datetime
from typing import Any, Dict, Optional

from location_resolver import LocationResolver, ResolvedLocation
from qweather_client import QWeatherClient


class WeatherService:
    """Weather service orchestrator tying LocationResolver, Client, and QueryPlanning."""

    def __init__(self, api_host: str = "", api_key: str = "", default_city: str = ""):
        self.api_host = (api_host or "").strip()
        self.api_key = (api_key or "").strip()
        self.default_city = (default_city or "").strip()

        # If api_host is configured, initialize real client; otherwise pass dummy or lazy client
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

        # 1. Resolve Location
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

        when = (when_text or "now").strip()
        focus = (focus_text or "general").strip()
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return {
            "status": "ok",
            "source": {
                "data_kinds": ["mock"],
                "retrieved_at_utc": now_utc,
            },
            "location": loc_dict,
            "location_resolution": {
                "method": loc_res.resolution_method,
            },
            "query": {
                "focus": focus,
                "normalized_when": when,
                "time_resolution": {
                    "status": "explicit" if when != "now" else "fallback_now",
                    "reason": None if when != "now" else "time_unspecified",
                },
                "target_start_local": "2026-11-04T00:00:00+08:00",
                "target_end_local": "2026-11-05T00:00:00+08:00",
                "granularity": "daily",
            },
            "weather": {
                "condition": "多云",
                "temperature_c": 18.0,
                "feels_like_c": 17.5,
                "temperature_min_c": 12.0,
                "temperature_max_c": 21.0,
                "wind_speed_ms": 3.0,
                "precipitation_probability": 0.0,
                "precipitation_mm": 0.0,
                "relevant_hours": [],
            },
            "current_snapshot": None,
            "comparison": {
                "available": False,
                "reason": "insufficient_comparable_data",
            },
            "warnings": [],
            "from_cache": False,
        }

    async def close(self) -> None:
        """Release underlying HTTP client / connections."""
        if self.client:
            await self.client.close()
