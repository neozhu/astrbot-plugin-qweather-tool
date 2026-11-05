import logging
import math
import time
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
            "User-Agent": "astrbot-plugin-qweather-tool/0.1.3",
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
            logger.error(f"GeoAPI search error: {type(e).__name__}")
            return []

    async def get_current_weather(
        self,
        lat: float,
        lon: float,
        lang: str = "zh",
        local_time: bool = False,
    ) -> Dict[str, Any]:
        """Fetch real-time weather from /weather/v1/current/{lat:.2f}/{lon:.2f} (SPEC §10.2, §10.3)."""
        try:
            resp = await self._request_current(lat, lon, lang, local_time)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "temperature" in data:
                    return data
                logger.warning("QWeather current API returned no weather data")
                return {}
            logger.warning(f"QWeather current API HTTP error: status={resp.status_code}")
            return {}
        except Exception as e:
            logger.error(f"Current weather request error: {type(e).__name__}")
            return {}

    async def _request_current(self, lat: float, lon: float, lang: str = "zh", local_time: bool = False):
        """Use the exact same endpoint, authentication and transport for queries and checks."""
        return await self._get_client().get(
            f"{self.api_host}/weather/v1/current/{float(lat):.2f}/{float(lon):.2f}",
            params={"lang": lang, "localTime": "true" if local_time else "false"},
            headers=self._build_headers(), timeout=self.timeout,
        )

    async def test_connection(self) -> Dict[str, Any]:
        """Validate host/key with one uncached current-weather request, not an API sweep."""
        started = time.perf_counter()

        def result(success, code, message):
            return {"success": success, "code": code, "message": message,
                    "latency_ms": int((time.perf_counter() - started) * 1000)}

        try:
            response = await self._request_current(39.90, 116.41)
            try:
                data = response.json()
            except ValueError:
                data = None

            code = str(response.status_code)
            if response.status_code == 200:
                code = "INVALID_RESPONSE"
                if isinstance(data, dict):
                    provider_error = data.get("error")
                    provider_code = str(provider_error.get("status")) if isinstance(provider_error, dict) else str(data.get("code", "200"))
                    if provider_code == "200":
                        value = data.get("temperature")
                        value = value.get("value") if isinstance(value, dict) else value
                        try:
                            valid = not isinstance(value, bool) and math.isfinite(float(value))
                        except (ValueError, TypeError, OverflowError):
                            valid = False
                        if valid:
                            return result(True, "200", "连接成功，实时天气接口调用正常，API 域名与 Key 可用。")
                    elif provider_code in {"401", "402", "403", "404", "429", "500", "503"}:
                        code = provider_code

            messages = {
                "401": "认证失败，请核对已保存的 API Key。",
                "402": "天气服务账户余额或配额不足，请检查账户状态。",
                "403": "实时天气接口拒绝访问，请核对 API 域名、Key 所属项目及接口权限。",
                "404": "未找到实时天气接口，请核对专属 API 域名。",
                "429": "天气服务请求被限流，请稍后重试；这不代表 Key 无效。",
                "INVALID_RESPONSE": "接口未返回有效的实时天气数据，请核对 API 域名或代理响应。",
            }
            return result(False, code, messages.get(code, "天气服务暂时不可用，请稍后重试。"))
        except httpx.TimeoutException:
            return result(False, "TIMEOUT", "实时天气接口请求超时，请检查服务器网络或代理后重试。")
        except httpx.RequestError:
            return result(False, "CONNECTION_FAILED", "无法连接天气服务，请检查服务器网络、DNS、代理或证书配置。")

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
            logger.error(f"Daily forecast request error: {type(e).__name__}")
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
            logger.error(f"Hourly forecast request error: {type(e).__name__}")
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
            logger.error(f"Historical weather request error: {type(e).__name__}")
            return None

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get_optional(
        self, path: str, params: Dict[str, Any], field: str, legacy: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Fetch supplementary data without confusing unavailable with an empty result.

        Never log raw request exceptions: their URLs can contain credentials.
        """
        try:
            resp = await self._get_client().get(
                f"{self.api_host}{path}", params=params,
                headers=self._build_headers(), timeout=min(self.timeout, 4.0),
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict) or (legacy and str(data.get("code")) != "200"):
                return None
            items = data.get(field)
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                return None
            return data
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    async def get_indices(self, location_id: str) -> Optional[Dict[str, Any]]:
        """Three-day sport, car wash, clothing and UV indices, subject to entitlement."""
        return await self._get_optional(
            "/v7/indices/3d", {"location": location_id, "type": "1,2,3,5", "lang": "zh"},
            "daily", legacy=True,
        )

    async def get_air_quality(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        return await self._get_optional(
            f"/airquality/v1/current/{lat:.2f}/{lon:.2f}", {"lang": "zh"}, "indexes",
        )

    async def get_warnings(self, location_id: str) -> Optional[Dict[str, Any]]:
        return await self._get_optional(
            "/v7/warning/now", {"location": location_id, "lang": "zh"}, "warning", legacy=True,
        )
