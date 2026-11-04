from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def daily_v1_data():
    with open(FIXTURES_DIR / "qweather_v1_daily.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def hourly_v1_data():
    with open(FIXTURES_DIR / "qweather_v1_hourly.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_timezone_conversion_and_calendar_dates_c24_c25():
    """SPEC §7.1, §7.2, C24, C25:
    Relative dates MUST be resolved according to target location's timezone.
    Even if clock is UTC 16:30 on Nov 4 (which is Europe Nov 4 17:30),
    in Asia/Shanghai it is Nov 5 00:30, so 'today' is 2026-11-05!
    """
    from time_resolver import TimeResolver

    # UTC 2026-11-04 16:30:00 -> Asia/Shanghai is 2026-11-05 00:30:00
    fixed_utc = datetime(2026, 11, 4, 16, 30, 0, tzinfo=timezone.utc)
    resolver = TimeResolver(clock=lambda: fixed_utc)

    res_today = resolver.resolve("today", tz="Asia/Shanghai")
    assert res_today.target.start_local.startswith("2026-11-05T00:00:00")
    assert res_today.target.end_local.startswith("2026-11-06T00:00:00")

    res_tomorrow = resolver.resolve("tomorrow", tz="Asia/Shanghai")
    assert res_tomorrow.target.start_local.startswith("2026-11-06T00:00:00")


def test_standard_grammar_and_chinese_aliases_c54_c55_c60():
    """SPEC §4.2, §7.2, §7.3, C54, C55, C60:
    Standard grammar and Chinese aliases resolve to identical half-open intervals.
    """
    from time_resolver import TimeResolver

    fixed_utc = datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)  # local 10:00
    resolver = TimeResolver(clock=lambda: fixed_utc)

    # +3d@evening vs 大后天傍晚
    t1 = resolver.resolve("+3d@evening", tz="Asia/Shanghai")
    t2 = resolver.resolve("大后天傍晚", tz="Asia/Shanghai")
    assert t1.target.start_local == t2.target.start_local
    assert t1.target.end_local == t2.target.end_local
    assert "18:00:00" in t1.target.start_local
    assert "2026-11-07" in t1.target.start_local

    # tomorrow@07:00 vs 明早7点
    m1 = resolver.resolve("tomorrow@07:00", tz="Asia/Shanghai")
    m2 = resolver.resolve("明早7点", tz="Asia/Shanghai")
    assert m1.target.start_local == m2.target.start_local
    assert "07:00:00" in m1.target.start_local
    assert "2026-11-05" in m1.target.start_local

    # tomorrow@afternoon vs 明天下午
    a1 = resolver.resolve("tomorrow@afternoon", tz="Asia/Shanghai")
    a2 = resolver.resolve("明天下午", tz="Asia/Shanghai")
    assert a1.target.start_local == a2.target.start_local
    assert "12:00:00" in a1.target.start_local
    assert "18:00:00" in a1.target.end_local


def test_range_over_midnight_window_c23():
    """SPEC §4.2, §7.2, C23:
    'today@evening to tomorrow@morning' resolves across midnight from 18:00 to 09:00 next day.
    """
    from time_resolver import TimeResolver

    fixed_utc = datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)
    resolver = TimeResolver(clock=lambda: fixed_utc)

    res = resolver.resolve("today@evening to tomorrow@morning", tz="Asia/Shanghai")
    assert "2026-11-04T18:00:00" in res.target.start_local
    assert "2026-11-05T09:00:00" in res.target.end_local


def test_hourly_precip_probability_ratio_c34(hourly_v1_data):
    """SPEC §10.4, §10.6, C34:
    Hourly precipitation probability is normalized to 0..1 ratio (e.g. 0.31 for 31%).
    """
    from weather_models import normalize_hourly_forecast

    hours = normalize_hourly_forecast(hourly_v1_data["hourly"])
    assert len(hours) == 3
    assert hours[0].precipitation_probability == 0.31
    assert hours[0].temperature_c == 11.0
    assert hours[0].precipitation_mm == 0.5


@pytest.mark.asyncio
async def test_query_planner_routes_to_daily_without_current_c44(daily_v1_data):
    """SPEC §8.1, C44:
    when='today', focus='general' queries Daily forecast; does not call Current.
    """
    from weather_service import WeatherService
    from location_resolver import ResolvedLocation

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    loc = ResolvedLocation(
        id="101190404", name="昆山", adm1="江苏省", adm2="苏州市",
        country="中国", lat=31.38, lon=120.98, tz="Asia/Shanghai",
    )

    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=loc, candidates=[])
        with patch.object(service.client, "get_daily_forecast", return_value=daily_v1_data["daily"]) as mock_daily:
            with patch.object(service.client, "get_current_weather") as mock_curr:
                res = await service.query(None, location_text="昆山", when_text="today", focus_text="general")

                assert mock_daily.called
                assert not mock_curr.called  # MUST NOT call Current
                assert res["status"] == "ok"
                assert res["weather"]["temperature_max_c"] == 22.0
                assert res["weather"]["temperature_min_c"] == 13.0
                assert "forecast_daily" in res["source"]["data_kinds"]


@pytest.mark.asyncio
async def test_query_planner_routes_to_hourly_for_future_daypart_c45_c46(hourly_v1_data):
    """SPEC §8.1, C45, C46:
    when='tomorrow@morning', focus='rain' queries Hourly; does not call Current.
    """
    from weather_service import WeatherService
    from location_resolver import ResolvedLocation

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    loc = ResolvedLocation(
        id="101190404", name="昆山", adm1="江苏省", adm2="苏州市",
        country="中国", lat=31.38, lon=120.98, tz="Asia/Shanghai",
    )

    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=loc, candidates=[])
        with patch.object(service.client, "get_hourly_forecast", return_value=hourly_v1_data["hourly"]) as mock_hourly:
            with patch.object(service.client, "get_current_weather") as mock_curr:
                res = await service.query(None, location_text="昆山", when_text="tomorrow@morning", focus_text="rain")

                assert mock_hourly.called
                assert not mock_curr.called  # MUST NOT call Current
                assert res["status"] == "ok"
                assert "forecast_hourly" in res["source"]["data_kinds"]
                assert res["weather"]["precipitation_probability"] == 0.31


def test_focus_alias_mapping_and_fallback_c61():
    """SPEC §4.1, §8.2, C61:
    Focus aliases: '穿衣' -> 'clothing'; unrecognized -> 'general' + warning.
    """
    from weather_service import normalize_focus

    f1, w1 = normalize_focus("穿衣")
    assert f1 == "clothing"
    assert w1 is None

    f2, w2 = normalize_focus("降雨")
    assert f2 == "rain"
    assert w2 is None

    f3, w3 = normalize_focus("unknown_intent")
    assert f3 == "general"
    assert w3 == "focus_fallback_general"
