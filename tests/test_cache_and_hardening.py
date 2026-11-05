import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent


class MockAstrEvent:
    def __init__(
        self,
        platform_id="wechat",
        origin="chat_private_user1",
        session_id="session_user1",
        sender_id="user1",
        is_group=False,
    ):
        self.platform_id = platform_id
        self.unified_msg_origin = origin
        self.session_id = session_id
        self.sender_id = sender_id
        self.message_obj = AsyncMock()
        self.message_obj.is_group = is_group
        self.message_obj.group_id = "group1" if is_group else None
        self.message_obj.sender = AsyncMock(user_id=sender_id)

    def plain_result(self, text):
        return {"type": "plain_result", "content": text}


@pytest.mark.asyncio
async def test_cache_ttl_and_hit_c50():
    """SPEC §13.2, C50:
    Consecutive queries for the same current weather within TTL (10m)
    return from_cache=True without triggering duplicate upstream requests.
    """
    from cache import TTLCache, SingleFlight
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event = MockAstrEvent()

    fake_current = {
        "temperature": {"value": 20.0},
        "condition": {"text": "晴"},
    }

    mock_loc_kunshan = AsyncMock(
        status="ok",
        location=AsyncMock(id="101190404", name="昆山", adm1="江苏省", adm2="苏州市", country="中国", lat=31.38, lon=120.98, tz="Asia/Shanghai", to_dict=lambda: {"name": "昆山"}),
        candidates=[],
    )

    with patch.object(service.location_resolver, "resolve", new=AsyncMock(return_value=mock_loc_kunshan)), \
         patch.object(service.client, "get_current_weather", new=AsyncMock(return_value=fake_current)) as mock_client_curr:

        # Turn 1: Cache miss
        res1 = await service.query(event, location_text="昆山", when_text="now", focus_text="general")
        assert res1["status"] == "ok"
        assert res1["from_cache"] is False
        assert mock_client_curr.call_count == 1

        # Turn 2: Cache hit within TTL
        res2 = await service.query(event, location_text="昆山", when_text="now", focus_text="general")
        assert res2["status"] == "ok"
        assert res2["from_cache"] is True
        # Should not call upstream again
        assert mock_client_curr.call_count == 1


@pytest.mark.asyncio
async def test_single_flight_deduplication_c40():
    """SPEC §13.2, §16, C40:
    Two concurrent queries for the same forecast execute a single upstream request (Single-flight).
    """
    from cache import SingleFlight

    sf = SingleFlight()
    call_count = 0

    async def expensive_upstream():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return {"data": 42}

    # Run two identical concurrent calls
    t1 = asyncio.create_task(sf.execute("identical_key", expensive_upstream))
    t2 = asyncio.create_task(sf.execute("identical_key", expensive_upstream))

    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == {"data": 42}
    assert r2 == {"data": 42}
    assert call_count == 1  # Deduplicated!


@pytest.mark.asyncio
async def test_error_mapping_without_credential_leakage_c15_c16_c66():
    """SPEC §10.3, §11.2, §18, C15, C16, C66:
    Maps 401/403 to auth_error, 429 to rate_limited, 5xx to provider_unavailable.
    Ensures API Key is NEVER present in Tool Result or error payloads.
    """
    from weather_service import WeatherService
    from qweather_client import QWeatherError

    secret_key = "sensitive_secret_api_key_12345"
    service = WeatherService(api_host="https://test.qweatherapi.com", api_key=secret_key)
    event = MockAstrEvent()

    # 1. 401/403 auth error
    with patch.object(service.location_resolver, "resolve", side_effect=QWeatherError("auth_error", "Invalid API Key")):
        res_auth = await service.query(event, location_text="昆山", when_text="now", focus_text="general")
        assert res_auth["status"] == "auth_error"
        res_auth_str = str(res_auth)
        assert secret_key not in res_auth_str

    # 2. 429 rate limited
    with patch.object(service.location_resolver, "resolve", side_effect=QWeatherError("rate_limited", "Too many requests")):
        res_rate = await service.query(event, location_text="昆山", when_text="now", focus_text="general")
        assert res_rate["status"] == "rate_limited"
        assert secret_key not in str(res_rate)

    # 3. 5xx provider unavailable
    with patch.object(service.location_resolver, "resolve", side_effect=QWeatherError("provider_unavailable", "Server error")):
        res_5xx = await service.query(event, location_text="昆山", when_text="now", focus_text="general")
        assert res_5xx["status"] == "provider_unavailable"
        assert secret_key not in str(res_5xx)


@pytest.mark.asyncio
async def test_unexpected_query_error_returns_provider_unavailable():
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    try:
        with patch.object(service.location_resolver, "resolve", side_effect=RuntimeError("unexpected failure")):
            result = await service.query(MockAstrEvent(), "昆山", "now", "general")

        assert result == {
            "status": "provider_unavailable",
            "warnings": ["provider_unavailable"],
        }
    finally:
        await service.close()


def test_zero_vendor_attribution_in_tool_result_c36_c37():
    """SPEC §0.2, §11.1, §12.2 #11, C36, C37:
    Tool Result must NOT contain QWeather branding, logos, URLs, or promotional text.
    """
    import json
    from weather_models import normalize_current_weather

    sample_api_json = {
        "code": "200",
        "temperature": {"value": 22.0},
        "condition": {"text": "晴"},
        "metadata": {
            "attributions": ["QWeather Data Service", "https://www.qweather.com"],
            "url": "https://www.qweather.com",
        },
    }

    norm = normalize_current_weather(sample_api_json)
    norm_dict = norm.to_dict()
    norm_json_str = json.dumps(norm_dict, ensure_ascii=False)

    assert "qweather" not in norm_json_str.lower()
    assert "https://" not in norm_json_str
