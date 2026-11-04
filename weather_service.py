import datetime
from typing import Any, Dict


class WeatherService:
    """Weather service orchestrator.
    In Issue #1, this serves as the foundational skeleton providing mock structured results
    conforming to SPEC-v1.3 §11 before real QWeather API integration in subsequent issues.
    """

    def __init__(self, api_host: str = "", api_key: str = "", default_city: str = ""):
        self.api_host = api_host
        self.api_key = api_key
        self.default_city = default_city

    async def query(
        self,
        event: Any,
        location_text: str,
        when_text: str,
        focus_text: str,
    ) -> Dict[str, Any]:
        """Process weather query and return structured Tool Result."""
        location = (location_text or "").strip()
        when = (when_text or "now").strip()
        focus = (focus_text or "general").strip()

        # If location is empty and no default city applies, return need_location
        if not location:
            if self.default_city:
                location = self.default_city
            else:
                return {
                    "status": "need_location",
                    "warnings": [],
                }

        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Minimal structured result following SPEC §11.1
        return {
            "status": "ok",
            "source": {
                "data_kinds": ["mock"],
                "retrieved_at_utc": now_utc,
            },
            "location": {
                "id": "mock-loc-1",
                "name": location,
                "adm1": "",
                "adm2": "",
                "country": "中国",
                "timezone": "Asia/Shanghai",
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
        pass
