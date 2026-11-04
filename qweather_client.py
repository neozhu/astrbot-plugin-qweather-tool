import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)


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

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
