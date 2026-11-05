import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)


class QWeatherError(Exception):
    """Categorized QWeather exception without sensitive credential details."""

    def __init__(self, error_code: str, message: str = ""):
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class QWeatherClient:
    """HTTP client for QWeather API services conforming to SPEC-v1.3 §10."""

    def __init__(self, api_host: str, api_key: str, timeout: float = 10.0):
        cleaned_host = (api_host or "").strip()
        if not cleaned_host.startswith("https://"):
            if cleaned_host.startswith("http://"):
                raise ValueError("Insecure HTTP api_host is prohibited; HTTPS is required.")
            cleaned_host = f"https://{cleaned_host}"

        parsed = urlparse(cleaned_host)
        if not parsed.netloc:
            raise ValueError(f"Invalid API Host: {api_host}")

        self.api_host = cleaned_host.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "astrbot-plugin-qweather-tool/0.1.0",
        }
        if self.api_key:
            headers["X-QW-Api-Key"] = self.api_key
        return headers

    async def search_city(
        self,
        location: str,
        adm: str = "",
        range_val: str = "",
        number: int = 10,
        lang: str = "zh",
    ) -> List[Dict[str, Any]]:
        """Search cities via GeoAPI GET /geo/v2/city/lookup (SPEC §6.2)."""
        loc = (location or "").strip()
        if not loc:
            return []

        url = f"{self.api_host}/geo/v2/city/lookup"
        params: Dict[str, Any] = {
            "location": loc,
            "number": number,
            "lang": lang,
        }
        if adm:
            params["adm"] = adm
        if range_val:
            params["range"] = range_val

        client = self._get_client()
        try:
            resp = await client.get(url, params=params, headers=self._build_headers())
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "200":
                    return data.get("location", [])
                elif data.get("code") in ["401", "403"]:
                    logger.warning(f"QWeather GeoAPI auth failed: code={data.get('code')}")
                elif data.get("code") == "404":
                    return []
            return []
        except Exception as e:
            logger.error(f"GeoAPI search error: {e}")
            return []

    async def get_current_weather(
        self,
        lat: float,
        lon: float,
        lang: str = "zh",
        local_time: bool = False,
    ) -> Dict[str, Any]:
        """Fetch real-time weather from /weather/v1/current/{lat:.2f}/{lon:.2f} (SPEC §10.2, §10.3)."""
        lat_formatted = f"{float(lat):.2f}"
        lon_formatted = f"{float(lon):.2f}"

        url = f"{self.api_host}/weather/v1/current/{lat_formatted}/{lon_formatted}"
        params: Dict[str, Any] = {
            "lang": lang,
            "localTime": "true" if local_time else "false",
        }

        client = self._get_client()
        try:
            resp = await client.get(url, params=params, headers=self._build_headers())
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "temperature" in data:
                    return data
                logger.warning("QWeather current API returned no weather data")
                return {}
            logger.warning(f"QWeather current API HTTP error: status={resp.status_code}")
            return {}
        except Exception as e:
            logger.error(f"Current weather request error: {e}")
            return {}

    async def get_daily_forecast(
        self,
        lat: float,
        lon: float,
        days: int = 7,
        lang: str = "zh",
    ) -> List[Dict[str, Any]]:
        """Fetch daily forecast from /weather/v1/daily/{lat:.2f}/{lon:.2f} (SPEC §10.2)."""
        lat_formatted = f"{float(lat):.2f}"
        lon_formatted = f"{float(lon):.2f}"

        url = f"{self.api_host}/weather/v1/daily/{lat_formatted}/{lon_formatted}"
        params: Dict[str, Any] = {
            "days": days,
            "lang": lang,
        }

        client = self._get_client()
        try:
            resp = await client.get(url, params=params, headers=self._build_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data.get("days", [])
            return []
        except Exception as e:
            logger.error(f"Daily forecast request error: {e}")
            return []

    async def get_hourly_forecast(
        self,
        lat: float,
        lon: float,
        hours: int = 24,
        lang: str = "zh",
    ) -> List[Dict[str, Any]]:
        """Fetch hourly forecast from /weather/v1/hourly/{lat:.2f}/{lon:.2f} (SPEC §10.2)."""
        lat_formatted = f"{float(lat):.2f}"
        lon_formatted = f"{float(lon):.2f}"

        url = f"{self.api_host}/weather/v1/hourly/{lat_formatted}/{lon_formatted}"
        params: Dict[str, Any] = {
            "hours": hours,
            "lang": lang,
        }

        client = self._get_client()
        try:
            resp = await client.get(url, params=params, headers=self._build_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data.get("hours", [])
            return []
        except Exception as e:
            logger.error(f"Hourly forecast request error: {e}")
            return []

    async def get_historical_weather(
        self,
        location_id: str,
        date_str: str,
        lang: str = "zh",
    ) -> Optional[Dict[str, Any]]:
        """Fetch historical weather from /v7/historical/weather?location={location_id}&date={yyyyMMdd} (SPEC §10.2, §10.5)."""
        url = f"{self.api_host}/v7/historical/weather"
        params: Dict[str, Any] = {
            "location": str(location_id),
            "date": str(date_str),
            "lang": lang,
        }

        client = self._get_client()
        try:
            resp = await client.get(url, params=params, headers=self._build_headers())
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "200":
                    return data
                logger.warning(f"QWeather historical API returned code={data.get('code')}")
                return None
            elif resp.status_code in (401, 403):
                logger.warning(f"QWeather historical API unauthorized: status={resp.status_code}")
                return None
            return None
        except Exception as e:
            logger.error(f"Historical weather request error: {e}")
            return None

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
