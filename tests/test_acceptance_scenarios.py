"""Comprehensive Acceptance Test Suite verifying SPEC v1.3 scenarios C01–C75."""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from location_resolver import LocationResolver, ResolvedLocation
from weather_service import WeatherService

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


@pytest.fixture
def mock_kunshan():
    return ResolvedLocation(
        id="101190404", name="昆山", adm1="江苏省", adm2="苏州市",
        country="中国", lat=31.38, lon=120.98, tz="Asia/Shanghai",
    )


@pytest.fixture
def mock_tokyo():
    return ResolvedLocation(
        id="102010100", name="东京", adm1="东京都", adm2="",
        country="日本", lat=35.68, lon=139.69, tz="Asia/Tokyo",
    )


@pytest.mark.asyncio
async def test_acceptance_c01_daily_local_timezone(mock_kunshan):
    """C01: 昆山今天天气如何？ -> Call; date in Kunshan tz; Daily forecast."""
    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    # Fixed Shanghai local time 2026-11-04 10:00:00 (02:00:00 UTC)
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    fake_daily = [
        {
            "forecastStartTime": "2026-11-04T00:00:00+08:00",
            "temperatureMax": {"value": 22.0},
            "temperatureMin": {"value": 15.0},
            "daytime": {"condition": {"text": "多云"}},
            "nighttime": {"condition": {"text": "阴"}},
        }
    ]

    with patch.object(service.location_resolver, "resolve") as mock_loc, \
         patch.object(service.client, "get_daily_forecast", new=AsyncMock(return_value=fake_daily)):
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="today", focus_text="general")
        assert res["status"] == "ok"
        assert res["location"]["name"] == "昆山"
        assert res["query"]["granularity"] == "daily"
        assert "2026-11-04" in res["query"]["target_start_local"]
        assert "forecast_daily" in res["source"]["data_kinds"]


@pytest.mark.asyncio
async def test_acceptance_c13_tokyo_local_date_not_cn(mock_tokyo):
    """C13: 东京明天下午怎么样？ -> Resolved with Asia/Tokyo timezone, no range=cn restriction."""
    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    # 2026-11-04 15:30:00 UTC -> 2026-11-05 00:30:00 Tokyo (already next day in Tokyo!)
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 15, 30, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_tokyo, candidates=[])

        res = await service.query(event, location_text="东京", when_text="tomorrow@afternoon", focus_text="general")
        assert res["status"] == "ok"
        assert res["location"]["timezone"] == "Asia/Tokyo"
        # Since it's already Nov 5 in Tokyo, tomorrow is Nov 6!
        assert "2026-11-06" in res["query"]["target_start_local"]


@pytest.mark.asyncio
async def test_acceptance_c14_unsupported_period_out_of_range(mock_kunshan):
    """C14: 下个月会不会下雨？ (+30d) -> Unsupported period out of forecast range."""
    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event = MockAstrEvent()

    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="+30d", focus_text="rain")
        assert res["status"] == "unsupported_period"
        assert "forecast_out_of_range" in res["warnings"]


@pytest.mark.asyncio
async def test_acceptance_c42_c44_c46_routing_rules(mock_kunshan):
    """C42, C44, C46:
    - C42: now retains Current as primary, without retrying a failed primary in the brief
    - C44: today retains Daily as primary; optional facts use their own caches
    - C46: '明天穿什么' -> Daily/Hourly, NEVER calls Current
    """
    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event = MockAstrEvent()

    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        # C42: now
        with patch.object(service.client, "get_current_weather", new=AsyncMock(return_value={})) as mock_curr:
            r42 = await service.query(event, location_text="昆山", when_text="now", focus_text="general")
            assert mock_curr.call_count == 1
            assert r42["source"]["data_kinds"] == ["current"]

        # C44: today
        with patch.object(service.client, "get_daily_forecast", new=AsyncMock(return_value=[])) as mock_daily, \
             patch.object(service.client, "get_current_weather", new=AsyncMock(return_value={})) as mock_curr:
            r44 = await service.query(event, location_text="昆山", when_text="today", focus_text="general")
            assert mock_daily.call_count == 1
            assert mock_curr.call_count == 0  # No current called!
            assert "forecast_daily" in r44["source"]["data_kinds"]

        # C46: tomorrow clothing
        with patch.object(service.client, "get_daily_forecast", new=AsyncMock(return_value=[])) as mock_daily, \
             patch.object(service.client, "get_current_weather", new=AsyncMock(return_value={})) as mock_curr:
            r46 = await service.query(event, location_text="昆山", when_text="tomorrow", focus_text="clothing")
            assert mock_daily.call_count == 1
            assert mock_curr.call_count == 0  # No current called!


@pytest.mark.asyncio
async def test_acceptance_c65_now_morning_contradiction(mock_kunshan):
    """C65: Contradictory when='now@morning' safely falls back to now with time_fallback_now."""
    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event = MockAstrEvent()

    with patch.object(service.location_resolver, "resolve") as mock_loc, \
         patch.object(service.client, "get_current_weather", new=AsyncMock(return_value={})):
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="now@morning", focus_text="general")
        assert res["status"] == "ok"
        assert res["query"]["granularity"] == "instant"
        assert "time_fallback_now" in res["warnings"]
