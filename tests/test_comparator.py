from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from location_resolver import ResolvedLocation

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
def mock_suzhou():
    return ResolvedLocation(
        id="101190401", name="苏州", adm1="江苏省", adm2="苏州市",
        country="中国", lat=31.30, lon=120.58, tz="Asia/Shanghai",
    )


def test_comparator_delta_and_change_magnitude_c22():
    """SPEC §9.4, C22:
    Deterministic calculation: delta_temp_c = round(target - baseline, 1).
    Magnitude heuristic: <3 minor, 3-<5 notable, 5-<8 significant, >=8 major.
    """
    from temperature_comparator import TemperatureComparator

    comparator = TemperatureComparator()

    # Case 1: 30°C vs 24°C (delta = -6.0) -> significant
    res1 = comparator.compute_delta(target_temp=24.0, baseline_temp=30.0)
    assert res1["delta_temp_c"] == -6.0
    assert res1["change_magnitude"] == "significant"

    # Case 2: 12°C vs 6°C (delta = -6.0) -> significant
    res2 = comparator.compute_delta(target_temp=6.0, baseline_temp=12.0)
    assert res2["delta_temp_c"] == -6.0
    assert res2["change_magnitude"] == "significant"

    # Case 3: < 3°C delta
    res3 = comparator.compute_delta(target_temp=21.5, baseline_temp=20.0)
    assert res3["delta_temp_c"] == 1.5
    assert res3["change_magnitude"] == "minor"

    # Case 4: >= 8°C delta
    res4 = comparator.compute_delta(target_temp=10.0, baseline_temp=19.2)
    assert res4["delta_temp_c"] == -9.2
    assert res4["change_magnitude"] == "major"


def test_comparator_rejects_different_locations_or_windows_c17_c64(mock_kunshan, mock_suzhou):
    """SPEC §9.2, C17, C64:
    Reject comparison across different locations or mismatched time window lengths.
    """
    from temperature_comparator import TemperatureComparator

    comparator = TemperatureComparator()

    # Different locations
    res_loc = comparator.validate_and_compare(
        target_loc=mock_kunshan,
        baseline_loc=mock_suzhou,
        target_temps=[15.0, 16.0],
        baseline_temps=[18.0, 19.0],
        target_window_hours=2,
        baseline_window_hours=2,
        target_kind="forecast",
        baseline_kind="forecast",
    )
    assert res_loc["available"] is False
    assert res_loc["reason"] == "mismatched_location"

    # Mismatched window hours (e.g. 3 hours vs 6 hours)
    res_win = comparator.validate_and_compare(
        target_loc=mock_kunshan,
        baseline_loc=mock_kunshan,
        target_temps=[15.0, 16.0],
        baseline_temps=[18.0, 19.0, 20.0],
        target_window_hours=2,
        baseline_window_hours=3,
        target_kind="forecast",
        baseline_kind="forecast",
    )
    assert res_win["available"] is False
    assert res_win["reason"] == "mismatched_time_window"


@pytest.mark.asyncio
async def test_clothing_evidence_structure_c20_c55(mock_kunshan):
    """SPEC §9.5, C20, C55:
    focus="clothing" must return structured clothing_evidence payload
    containing target temperature, feels_like, wind, rain, and comparison.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    # Mock hourly forecast
    fake_hourly = [
        {
            "forecastTime": "2026-11-05T07:00:00+08:00",
            "temperature": {"value": 8.0},
            "feelsLike": {"value": 5.0},
            "condition": {"text": "小雨"},
            "wind": {"speed": {"value": 4.0}},
            "precipitation": {"amount": {"value": 1.2}, "probability": 0.8},
        }
    ]

    with patch.object(service.location_resolver, "resolve") as mock_loc, \
         patch.object(service.client, "get_hourly_forecast", new=AsyncMock(return_value=fake_hourly)):
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="tomorrow@07:00", focus_text="clothing")
        assert res["status"] == "ok"
        assert "clothing_evidence" in res
        evidence = res["clothing_evidence"]
        assert evidence["target_temperature_c"] == 8.0
        assert evidence["target_feels_like_c"] == 5.0
        assert evidence["target_wind_speed_ms"] == 4.0
        assert evidence["rain_expected"] is True


@pytest.mark.asyncio
async def test_tomorrow_vs_today_comparison_c17_c56(mock_kunshan):
    """SPEC §9.3, C17, C56:
    when="tomorrow vs today", focus="comparison" compares matching daily or hourly forecasts.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    # Fixed time: 2026-11-04 10:00:00 Shanghai
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    fake_daily = [
        {
            "forecastStartTime": "2026-11-04T00:00:00+08:00",
            "temperatureMax": {"value": 20.0},
            "temperatureMin": {"value": 10.0},
            "daytime": {"condition": {"text": "晴"}},
            "nighttime": {"condition": {"text": "多云"}},
        },
        {
            "forecastStartTime": "2026-11-05T00:00:00+08:00",
            "temperatureMax": {"value": 14.0},
            "temperatureMin": {"value": 6.0},
            "daytime": {"condition": {"text": "阴"}},
            "nighttime": {"condition": {"text": "小雨"}},
        },
    ]

    with patch.object(service.location_resolver, "resolve") as mock_loc, \
         patch.object(service.client, "get_daily_forecast", new=AsyncMock(return_value=fake_daily)):
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="tomorrow vs today", focus_text="comparison")
        assert res["status"] == "ok"
        assert res["comparison"]["available"] is True
        assert res["comparison"]["delta_temp_c"] == -6.0  # (14+6)/2 = 10 vs (20+10)/2 = 15 => -5, or Max vs Max (14 - 20 = -6)
        assert res["comparison"]["target_kind"] == "forecast"
        assert res["comparison"]["baseline_kind"] == "forecast"


@pytest.mark.asyncio
async def test_historical_v7_yesterday_comparison_c18_c47(mock_kunshan):
    """SPEC §9.2 #5, §10.2, C18, C47:
    when="now vs yesterday", queries Current + Historical v7, tagging historical_reanalysis vs current.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    # Fixed time: 2026-11-04 14:00:00 Shanghai
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 6, 0, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    fake_current = {
        "temperature": {"value": 18.0},
        "feelsLike": {"value": 17.5},
        "condition": {"text": "多云"},
        "wind": {"speed": {"value": 2.5}},
        "precipitation": {"amount": {"value": 0.0}},
    }

    fake_historical = {
        "weatherDaily": {"date": "2026-11-03", "tempMax": "22", "tempMin": "14"},
        "weatherHourly": [
            {"time": "2026-11-03 14:00", "temp": "21", "windSpeed": "10"}
        ],
    }

    with patch.object(service.location_resolver, "resolve") as mock_loc, \
         patch.object(service.client, "get_current_weather", new=AsyncMock(return_value=fake_current)), \
         patch.object(service.client, "get_historical_weather", new=AsyncMock(return_value=fake_historical)):
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="now vs yesterday", focus_text="comparison")
        assert res["status"] == "ok"
        assert res["comparison"]["available"] is True
        assert res["comparison"]["target_temp_c"] == 18.0
        assert res["comparison"]["baseline_temp_c"] == 21.0
        assert res["comparison"]["delta_temp_c"] == -3.0
        assert res["comparison"]["target_kind"] == "current"
        assert res["comparison"]["baseline_kind"] == "historical_reanalysis"


@pytest.mark.asyncio
async def test_historical_unauthorized_graceful_downgrade_c19(mock_kunshan):
    """SPEC §8.4, §10.3, §11.2, C19:
    When Historical API returns 401/403 or None, returns partial status with
    warning historical_not_entitled, without failing or fabricating yesterday's data.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 6, 0, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    fake_current = {
        "temperature": {"value": 18.0},
        "feelsLike": {"value": 17.5},
        "condition": {"text": "多云"},
        "wind": {"speed": {"value": 2.5}},
        "precipitation": {"amount": {"value": 0.0}},
    }

    with patch.object(service.location_resolver, "resolve") as mock_loc, \
         patch.object(service.client, "get_current_weather", new=AsyncMock(return_value=fake_current)), \
         patch.object(service.client, "get_historical_weather", new=AsyncMock(return_value=None)):
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="now vs yesterday", focus_text="comparison")
        assert res["status"] == "partial"
        assert "historical_not_entitled" in res["warnings"]
        assert res["comparison"]["available"] is False
        assert res["comparison"]["reason"] == "historical_not_entitled"
        # Current data is still provided truthfully!
        assert res["weather"]["temperature_c"] == 18.0


@pytest.mark.asyncio
async def test_past_hour_protection_does_not_fake_current_c48(mock_kunshan):
    """SPEC §8.1, §9.3, C48:
    At 15:00 asking about today@07:00:
    Does NOT use Current weather to fake passed morning hour data.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    # Current time: 15:00 Shanghai (07:00 UTC)
    service.time_resolver.clock = lambda: datetime(2026, 11, 4, 7, 0, 0, tzinfo=timezone.utc)
    event = MockAstrEvent()

    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])

        res = await service.query(event, location_text="昆山", when_text="today@07:00", focus_text="general")
        # Should be partial or indicate past hour cannot be faked with current
        assert res["status"] == "partial"
        assert "past_hour_unsupported" in res["warnings"]
        assert res["weather"]["temperature_c"] is None
