import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["current", "daily", "hourly"])
async def test_v1_client_reads_unwrapped_response(kind, current_v1_data):
    """v1 uses root weather fields, days and hours, without a code/now wrapper."""
    import httpx
    from qweather_client import QWeatherClient

    current = current_v1_data["now"]
    daily = [{"forecastStartTime": "2026-11-05T07:00+08:00", "temperatureMax": {"value": 22}}]
    hourly = [{"forecastTime": "2026-11-05T08:00+08:00", "temperature": {"value": 18}}]
    payload = {"current": current, "daily": {"days": daily}, "hourly": {"hours": hourly}}[kind]
    expected = {"current": current, "daily": daily, "hourly": hourly}[kind]
    client = QWeatherClient(api_host="test.qweatherapi.com", api_key="test_key")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    ))
    try:
        result = await getattr(client, f"get_{'current_weather' if kind == 'current' else kind + '_forecast'}")(31.38, 120.98)
        assert result == expected
    finally:
        await client.close()


@pytest.fixture
def current_v1_data():
    with open(FIXTURES_DIR / "qweather_v1_current.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_client_formats_coordinates_to_two_decimals_c67():
    """SPEC §10.2, §10.3, C67:
    v1 coordinates in path must be formatted to at most 2 decimal places:
    e.g. 31.384612, 120.981324 -> /weather/v1/current/31.38/120.98.
    """
    from qweather_client import QWeatherClient

    client = QWeatherClient(api_host="https://test.qweatherapi.com", api_key="test_key")

    with patch.object(client, "_get_client") as mock_get_client:
        mock_http = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {"code": "200", "now": {}}
        mock_http.get.return_value = mock_resp
        mock_get_client.return_value = mock_http

        await client.get_current_weather(lat=31.384612, lon=120.981324)

        assert mock_http.get.called
        call_url = mock_http.get.call_args[0][0]
        assert "/weather/v1/current/31.38/120.98" in call_url


def test_current_weather_normalizer_nested_fields_and_no_precip_probability_c49(current_v1_data):
    """SPEC §10.4, §10.6, C49:
    Normalizer maps nested fields: temperature, feelsLike, condition, wind, precip amount.
    Current does NOT have precip probability; it must remain None, NEVER 0.
    """
    from weather_models import normalize_current_weather

    res = normalize_current_weather(current_v1_data["now"])

    assert res.temperature_c == 18.5
    assert res.feels_like_c == 17.0
    assert res.condition == "多云"
    assert res.wind_speed_ms == 3.2
    assert res.precipitation_mm == 0.0
    assert res.precipitation_probability is None  # MUST NOT be 0


@pytest.mark.asyncio
async def test_default_now_unspecified_time_c51_c53(current_v1_data):
    """SPEC §0.1 #10, §4.5, §7.4, §11.1, C51, C53:
    When when is empty and no context exists:
    time_resolution status is 'fallback_now', reason is 'time_unspecified',
    queries Current weather.
    """
    from weather_service import WeatherService
    from location_resolver import ResolvedLocation

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")

    mock_loc = ResolvedLocation(
        id="101190404",
        name="昆山",
        adm1="江苏省",
        adm2="苏州市",
        country="中国",
        lat=31.3846,
        lon=120.9813,
        tz="Asia/Shanghai",
    )

    with patch.object(service.location_resolver, "resolve") as mock_res:
        mock_res.return_value = AsyncMock(status="ok", location=mock_loc, candidates=[])
        with patch.object(service.client, "get_current_weather", return_value=current_v1_data["now"]):
            result = await service.query(
                event=None,
                location_text="昆山",
                when_text="",
                focus_text="general",
            )

    assert result["status"] == "ok"
    assert result["query"]["normalized_when"] == "now"
    assert result["query"]["time_resolution"]["status"] == "fallback_now"
    assert result["query"]["time_resolution"]["reason"] == "time_unspecified"
    assert result["current_snapshot"] is not None
    assert result["current_snapshot"]["temperature_c"] == 18.5
    assert "current" in result["source"]["data_kinds"]


@pytest.mark.asyncio
async def test_fallback_now_unrecognized_explicit_time_c59(current_v1_data):
    """SPEC §4.5, §7.4 #5, C59:
    When explicit time cannot be recognized:
    fallbacks to now, reason is 'unrecognized_explicit_time',
    queries Current weather, does not invent future weather.
    """
    from weather_service import WeatherService
    from location_resolver import ResolvedLocation

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")

    mock_loc = ResolvedLocation(
        id="101190404",
        name="昆山",
        adm1="江苏省",
        adm2="苏州市",
        country="中国",
        lat=31.3846,
        lon=120.9813,
        tz="Asia/Shanghai",
    )

    with patch.object(service.location_resolver, "resolve") as mock_res:
        mock_res.return_value = AsyncMock(status="ok", location=mock_loc, candidates=[])
        with patch.object(service.client, "get_current_weather", return_value=current_v1_data["now"]):
            result = await service.query(
                event=None,
                location_text="昆山",
                when_text="无法确认的某个节日后",
                focus_text="general",
            )

    assert result["status"] == "ok"
    assert result["query"]["time_resolution"]["status"] == "fallback_now"
    assert result["query"]["time_resolution"]["reason"] == "unrecognized_explicit_time"
    assert result["current_snapshot"] is not None
    assert "time_fallback_now" in result.get("warnings", [])
