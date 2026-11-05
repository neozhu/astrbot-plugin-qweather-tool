import json
import asyncio
from collections import Counter
from datetime import datetime, timezone

import httpx
import pytest

from qweather_client import QWeatherClient
from weather_models import normalize_current_weather
from location_resolver import ResolvedLocation
from weather_service import WeatherService

NOW = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)
LOC = ResolvedLocation(id="101190404", name="昆山", adm1="江苏省", adm2="苏州市",
                       country="中国", lat=31.38, lon=120.98, tz="Asia/Shanghai")


def make_service(*, denied=(), hourly=None, indices=None, warnings=None, air=None):
    calls = Counter()

    async def handle(request):
        path = request.url.path
        calls[path] += 1
        await asyncio.sleep(0)
        if any(kind in path for kind in denied):
            return httpx.Response(403, json={"code": "403"})
        if "/weather/v1/current/" in path:
            payload = {"temperature": {"value": 16}, "feelsLike": {"value": 14},
                       "condition": {"text": "多云"}, "humidity": 72,
                       "wind": {"speed": {"value": 3.2}, "direction": "东北风"},
                       "obsTime": "2026-11-05T09:00:00+08:00"}
        elif "/daily/" in path:
            payload = {"days": [
                {"fxDate": day, "temperatureMax": {"value": high}, "temperatureMin": {"value": low},
                 "daytime": {"condition": {"text": "多云"}}, "nighttime": {"condition": {"text": "阴"}}}
                for day, high, low in [("2026-11-05", 20, 11), ("2026-11-06", 18, 9)]
            ]}
        elif "/hourly/" in path:
            payload = {"hours": hourly if hourly is not None else [
                {"forecastTime": "2026-11-05T09:00:00+08:00", "temperature": {"value": 16},
                 "feelsLike": {"value": 14}, "precipitation": {"probability": 10}},
                {"forecastTime": "2026-11-05T15:00:00+08:00", "temperature": {"value": 19},
                 "feelsLike": {"value": 18}, "precipitation": {"probability": 70, "amount": {"value": 2}}},
                {"forecastTime": "2026-11-06T07:00:00+08:00", "temperature": {"value": 10}},
            ]}
        elif "/indices/" in path:
            payload = {"code": "200", "daily": indices if indices is not None else [
                {"date": "2026-11-05", "type": "3", "category": "较凉", "text": "长袖配外套"},
                {"date": "2026-11-06", "type": "3", "category": "冷", "text": "穿厚外套"},
            ]}
        elif "/airquality/" in path:
            payload = {"indexes": air if air is not None else [{"code": "chn-mee", "aqi": 62, "category": "良"}]}
        elif "/warning/" in path:
            payload = {"code": "200", "warning": warnings if warnings is not None else []}
        else:
            raise AssertionError(f"Unexpected endpoint: {path}")
        return httpx.Response(200, json=payload)

    service = WeatherService(api_host="test.qweatherapi.com", api_key="test-key")
    service.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    service.time_resolver.clock = lambda: NOW
    return service, calls


async def query(service, when="now"):
    return await service.query(None, "", when, "general", confirmed_location=LOC)


@pytest.mark.asyncio
async def test_default_brief_contains_feels_like_range_later_rain_and_indices():
    service, calls = make_service()
    try:
        result = await query(service)
        brief = result["brief"]
        assert result["weather"]["temperature_c"] == 16
        assert brief["current"]["feels_like_c"] == 14
        assert brief["current"]["relative_humidity"] == 0.72
        assert brief["daily"]["temperature_min_c"] == 11
        assert brief["daily"]["condition_night"] == "阴"
        assert brief["hourly"]["peak_hourly_precipitation_probability"] == 0.7
        assert brief["hourly"]["feels_like_min_c"] == 14
        assert brief["hourly"]["precipitation_mm"] is None  # one amount missing, not zero
        assert len(brief["hourly"]["hours"]) == 2  # next twelve hours, not tomorrow's morning
        assert brief["indices"][0]["category"] == "较凉"
        assert brief["air_quality"][0]["code"] == "chn-mee"
        assert brief["alerts"] == []
        assert not brief["warnings"]
        assert calls["/weather/v1/current/31.38/120.98"] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_future_brief_does_not_relabel_current_data_or_today_indices():
    service, calls = make_service()
    try:
        brief = (await query(service, "tomorrow"))["brief"]
        assert brief["current"] is None
        assert brief["air_quality"] is None
        assert brief["alerts"] is None
        assert brief["daily"]["date"] == "2026-11-06"
        assert [i["category"] for i in brief["indices"]] == ["冷"]
        assert len(brief["hourly"]["hours"]) == 1
        assert brief["hourly"]["hours"][0]["temperature_c"] == 10
        assert not any("/current/" in path or "/warning/" in path for path in calls)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_optional_denial_preserves_weather_and_no_false_clear():
    service, _ = make_service(denied=("indices", "airquality", "warning"))
    try:
        result = await query(service)
        assert result["weather"]["temperature_c"] == 16
        assert result["status"] == "ok"
        assert result["brief"]["alerts"] is None  # unavailable != no warnings
        assert result["brief"]["indices"] is None
        assert "indices_unavailable" in result["brief"]["warnings"]
        assert "test-key" not in json.dumps(result)
        assert "https://" not in json.dumps(result)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_brief_caches_and_deduplicates_supplementary_requests():
    service, calls = make_service()
    try:
        results = await asyncio.gather(query(service), query(service))
        await query(service)
        assert all(r["brief"]["daily"]["temperature_max_c"] == 20 for r in results)
        assert all(count == 1 for count in calls.values())
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_missing_hourly_data_does_not_become_zero_probability():
    service, _ = make_service(hourly=[])
    try:
        brief = (await query(service))["brief"]
        assert brief["hourly"] is None
        assert "hourly_unavailable" in brief["warnings"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_alerts_filter_expired_messages_and_keep_active_warning():
    service, _ = make_service(warnings=[
        {"id": "old", "title": "过期预警", "endTime": "2026-11-04T23:00:00+08:00"},
        {"id": "active", "title": "大风黄色预警", "endTime": "2026-11-05T18:00:00+08:00"},
    ])
    try:
        alerts = (await query(service))["brief"]["alerts"]
        assert [a["id"] for a in alerts] == ["active"]
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,path,params,payload", [
    ("indices", "/v7/indices/3d", {"location": "101190404", "type": "1,2,3,5", "lang": "zh"},
     {"code": "200", "daily": [{"date": "2026-11-05", "type": "3", "category": "较冷", "text": "建议穿外套"}]}),
    ("air_quality", "/airquality/v1/current/31.38/120.98", {"lang": "zh"},
     {"indexes": [{"code": "chn-mee", "aqi": 62, "category": "良"}]}),
    ("warnings", "/v7/warning/now", {"location": "101190404", "lang": "zh"},
     {"code": "200", "warning": []}),
])
async def test_optional_api_contract(kind, path, params, payload):
    def handle(request):
        assert request.url.path == path
        assert dict(request.url.params) == params
        assert request.headers["X-QW-Api-Key"] == "test-key"
        return httpx.Response(200, json=payload)

    client = QWeatherClient("test.qweatherapi.com", "test-key")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        args = (31.3846, 120.9813) if kind == "air_quality" else ("101190404",)
        assert await getattr(client, "get_" + kind)(*args) == payload
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(403, {}), (200, {"code": "403"}),
                                         (200, []), (200, {"code": "200", "daily": "bad"})])
async def test_optional_api_failure_is_not_empty_success(status, body):
    client = QWeatherClient("test.qweatherapi.com", "test-key")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json=body)))
    try:
        assert await client.get_indices("101190404") is None
    finally:
        await client.close()


def test_current_preserves_wind_humidity_and_observation():
    result = normalize_current_weather({
        "temperature": {"value": 16}, "feelsLike": {"value": 14},
        "wind": {"speed": {"value": 3.2}, "direction": "东北风"},
        "windGust": {"value": 7.5}, "humidity": 72,
        "obsTime": "2026-11-05T09:00:00+08:00",
    }).to_dict()
    assert result["wind_direction"] == "东北风"
    assert result["wind_gust_ms"] == 7.5
    assert result["relative_humidity"] == 0.72
    assert result["obs_time"] == "2026-11-05T09:00:00+08:00"


def test_daily_preserves_day_night_wind_for_future_briefs():
    from weather_models import normalize_daily_forecast

    result = normalize_daily_forecast([{
        "fxDate": "2026-11-06",
        "daytime": {"wind": {"speed": {"value": 4}, "direction": "北风"}},
        "nighttime": {"wind": {"speed": {"value": 2}, "direction": "东北风"}},
    }])[0].to_dict()
    assert result["wind_speed_day_ms"] == 4
    assert result["wind_direction_night"] == "东北风"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["wechat_native", "telegram"])
async def test_tool_returns_rich_facts_with_concise_plain_response_guidance(platform):
    from types import SimpleNamespace
    from main import QWeatherPlugin

    plugin = QWeatherPlugin(SimpleNamespace(), {"api_host": "test.qweatherapi.com"})
    service, _ = make_service()
    plugin.weather_service = service
    event = SimpleNamespace(get_platform_name=lambda: platform)
    scope = service.context_store.extract_scope_key(event)
    service.context_store.get_context(scope).set_confirmed_location(LOC, NOW)
    try:
        result = json.loads(await plugin.get_weather(event, "", "now", "general"))
        assert result["brief"]["current"]["feels_like_c"] == 14
        assert "一到两段" in result["response_guidance"]
        assert "不使用图标或表情" in result["response_guidance"]
        assert "体感" in result["response_guidance"]
        assert "逐小时" in result["response_guidance"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_hour_summary_uses_real_instants_and_excludes_end_boundary():
    service, _ = make_service(hourly=[
        {"forecastTime": "2026-11-05T04:00:00Z", "temperature": {"value": 18}},
        {"forecastTime": "2026-11-05T05:00:00Z", "temperature": {"value": 20}},
        {"forecastTime": "2026-11-05T10:00:00Z", "temperature": {"value": 99}},
    ])
    try:
        summary = (await query(service, "today@afternoon"))["brief"]["hourly"]
        assert summary["temperature_min_c"] == 18
        assert summary["temperature_max_c"] == 20
        assert summary["precipitation_mm"] is None
        assert summary["peak_hourly_precipitation_probability"] is None
        assert summary["last_forecast_time_local"] == "2026-11-05T13:00:00+08:00"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_indices_do_not_fall_back_to_another_date():
    service, _ = make_service(indices=[{"date": "2026-11-05", "type": "3", "category": "暖"}])
    try:
        brief = (await query(service, "tomorrow"))["brief"]
        assert brief["indices"] is None
        assert "indices_unavailable" in brief["warnings"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_slow_supplement_times_out_without_losing_primary_weather(monkeypatch):
    from weather_brief import WeatherBriefBuilder

    monkeypatch.setattr(WeatherBriefBuilder, "SOURCE_TIMEOUT_SECONDS", 0.01)
    service, _ = make_service()
    cancelled = asyncio.Event()

    async def slow_indices(location_id):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(service.client, "get_indices", slow_indices)
    try:
        result = await asyncio.wait_for(query(service), timeout=1)
        assert result["weather"]["temperature_c"] == 16
        assert "indices_unavailable" in result["brief"]["warnings"]
        assert cancelled.is_set()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_hourly_primary_does_not_hide_later_rain_or_missing_amount():
    service, _ = make_service(hourly=[
        {"forecastTime": "2026-11-05T12:00:00+08:00", "temperature": {"value": 18},
         "precipitation": {"probability": 10}},
        {"forecastTime": "2026-11-05T15:00:00+08:00", "temperature": {"value": 19},
         "precipitation": {"probability": 70, "amount": {"value": 2}}},
    ])
    try:
        result = await query(service, "today@afternoon")
        assert result["weather"]["precipitation_mm"] is None
        assert result["weather"]["precipitation_probability"] == 0.7
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_provider_exceptions_do_not_leak_into_logs(caplog):
    client = QWeatherClient("test.qweatherapi.com", "private-api-key")

    def fail(request):
        raise httpx.ConnectError("failed request with private-api-key", request=request)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    try:
        assert await client.get_current_weather(31.38, 120.98) == {}
        assert "private-api-key" not in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_owner", [True, False])
async def test_cancelling_one_singleflight_caller_does_not_strand_other_callers(cancel_owner):
    from cache import SingleFlight

    sf = SingleFlight()
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return 42

    owner = asyncio.create_task(sf.execute("shared", fetch))
    await started.wait()
    follower = asyncio.create_task(sf.execute("shared", fetch))
    await asyncio.sleep(0)
    cancelled = owner if cancel_owner else follower
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    survivor = follower if cancel_owner else asyncio.create_task(sf.execute("shared", fetch))
    await asyncio.sleep(0)
    release.set()
    try:
        assert await asyncio.wait_for(survivor, timeout=0.1) == 42
        if not cancel_owner:
            assert await owner == 42
        assert calls == 1
    finally:
        for task in (owner, follower, survivor):
            if not task.done():
                task.cancel()
        await asyncio.gather(owner, follower, survivor, return_exceptions=True)


@pytest.mark.asyncio
async def test_brief_deadline_applies_when_joining_slow_primary_request(monkeypatch):
    from cache import SingleFlight, TTLCache
    from weather_brief import WeatherBriefBuilder
    from time_resolver import TimeResolver

    service, _ = make_service()
    sf = SingleFlight()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_primary():
        started.set()
        await release.wait()
        return []

    owner = asyncio.create_task(sf.execute("daily:31.38:120.98:7", slow_primary))
    await started.wait()
    monkeypatch.setattr(WeatherBriefBuilder, "SOURCE_TIMEOUT_SECONDS", 0.01)
    builder = WeatherBriefBuilder(service.client, TTLCache(), sf)
    target = TimeResolver().resolve("now", LOC.tz, clock_now=NOW).target
    try:
        brief = await asyncio.wait_for(builder.build(LOC, target, NOW), timeout=0.2)
        assert brief["daily"] is None
        assert "daily_unavailable" in brief["warnings"]
        assert not owner.done()  # timing out this caller must not cancel shared work
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [{"aqi": 62}, {"code": "chn-mee", "aqi": "invalid"},
                                  {"code": "chn-mee", "aqi": -1}])
async def test_malformed_air_index_is_unavailable_not_health_advice(row):
    service, _ = make_service(air=[row])
    try:
        result = await query(service)
        assert result["brief"]["air_quality"] is None
        assert "air_quality_unavailable" in result["brief"]["warnings"]
        assert result["weather"]["temperature_c"] == 16
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_malformed_warning_is_unavailable_not_an_all_clear():
    service, _ = make_service(warnings=[{}])
    try:
        brief = (await query(service))["brief"]
        assert brief["alerts"] is None
        assert "alerts_unavailable" in brief["warnings"]
    finally:
        await service.close()
