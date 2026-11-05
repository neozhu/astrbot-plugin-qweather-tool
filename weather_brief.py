"""Supplementary weather facts; the host LLM remains responsible for prose."""
import asyncio
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from .cache import TTLCache
    from .weather_models import (
        normalize_current_weather, normalize_daily_forecast, normalize_hourly_forecast,
    )
except (ImportError, ValueError):
    from cache import TTLCache
    from weather_models import (
        normalize_current_weather, normalize_daily_forecast, normalize_hourly_forecast,
    )


def _local_time(value, tz):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    except (ValueError, TypeError, AttributeError):
        return None


def summarize_hours(hours, start, end, tz):
    """Summarize all available hours, preserving gaps and unknown rainfall."""
    selected = {}
    for hour in hours:
        stamp = _local_time(hour.forecast_time_local, tz)
        if stamp is not None and start <= stamp < end:
            selected[stamp] = hour.to_dict()
    if not selected:
        return None
    stamps = sorted(selected)
    rows = [selected[stamp] for stamp in stamps]

    def values(key):
        return [row[key] for row in rows if row.get(key) is not None]

    temperatures = values("temperature_c")
    feels_like = values("feels_like_c")
    probabilities = values("precipitation_probability")
    amounts = values("precipitation_mm")
    winds = values("wind_speed_ms")
    return {
        "requested_start_local": start.isoformat(),
        "requested_end_local": end.isoformat(),
        "first_forecast_time_local": stamps[0].isoformat(),
        "last_forecast_time_local": stamps[-1].isoformat(),
        "hours": rows,
        "temperature_min_c": min(temperatures, default=None),
        "temperature_max_c": max(temperatures, default=None),
        "feels_like_min_c": min(feels_like, default=None),
        "feels_like_max_c": max(feels_like, default=None),
        # This is explicitly a maximum of individual-hour probabilities, not
        # the probability of rain over the entire requested period.
        "peak_hourly_precipitation_probability": max(probabilities, default=None),
        "precipitation_mm": round(sum(amounts), 2) if len(amounts) == len(rows) else None,
        "wind_speed_max_ms": max(winds, default=None),
    }


def _air_indexes(data):
    indexes = []
    for item in data["indexes"]:
        try:
            aqi = float(item.get("aqi"))
        except (TypeError, ValueError):
            continue
        if (not isinstance(item.get("code"), str) or not item["code"].strip()
                or isinstance(item.get("aqi"), bool) or not math.isfinite(aqi) or aqi < 0):
            continue
        row = {key: item.get(key) for key in ("code", "name", "category", "primaryPollutant")}
        row["aqi"] = aqi
        indexes.append(row)
    return indexes or None


def _active_alerts(data, tz, now):
    alerts = []
    for item in data["warning"]:
        # A malformed warning cannot be interpreted as an all-clear.
        if not item.get("id") or not (item.get("title") or item.get("text")):
            return None
        end = _local_time(item.get("endTime"), tz)
        if item.get("status") == "cancel" or (end is not None and end <= now):
            continue
        alerts.append({key: item.get(key) for key in (
            "id", "title", "typeName", "severity", "level", "startTime", "endTime", "text",
        )})
    return alerts


class WeatherBriefBuilder:
    """Bounded concurrent collection sharing the service's cache and single-flight."""

    SOURCE_TIMEOUT_SECONDS = 4.5

    def __init__(self, client, cache, single_flight):
        self.client = client
        self.cache = cache
        self.single_flight = single_flight

    async def _cached(self, key, ttl, load):
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if self.cache.get("missing:" + key):
            return None

        async def fetch():
            # Cache is written inside the single-flight operation so another
            # caller cannot slip between completion and insertion.
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            try:
                value = await asyncio.wait_for(load(), timeout=self.SOURCE_TIMEOUT_SECONDS)
            except Exception:
                # No provider URL, key or exception text crosses this boundary.
                value = None
            if value is not None and value != []:
                self.cache.set(key, value, ttl)
            else:
                self.cache.set("missing:" + key, True, 60)
            return value

        try:
            # Include time waiting for an existing primary request in this
            # caller's budget. SingleFlight shields the shared task itself.
            return await asyncio.wait_for(
                self.single_flight.execute(key, fetch), timeout=self.SOURCE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return None

    async def build(self, loc, target, now):
        tz = ZoneInfo(loc.tz)
        now_local = now.astimezone(tz)
        start = _local_time(target.start_local, tz)
        end = _local_time(target.end_local, tz)
        if start is None or end is None or (target.granularity != "instant" and end <= now_local):
            return None
        day = start.date()
        today = day == now_local.date()
        if target.granularity == "instant":
            start = now_local
            end = start + timedelta(hours=12)
        elif target.granularity == "daily" and today:
            start = max(start, now_local)

        brief = {
            "date": day.isoformat(), "timezone": loc.tz,
            "current": None, "daily": None, "hourly": None,
            "indices": None, "air_quality": None, "alerts": None, "warnings": [],
        }
        position = f"{loc.lat:.2f}:{loc.lon:.2f}"

        async def load_current():
            raw = await self.client.get_current_weather(loc.lat, loc.lon)
            return normalize_current_weather(raw) if raw else None

        async def load_daily():
            return normalize_daily_forecast(await self.client.get_daily_forecast(loc.lat, loc.lon, days=7))

        async def load_hourly():
            return normalize_hourly_forecast(
                await self.client.get_hourly_forecast(loc.lat, loc.lon, hours=24), loc.tz,
            )

        async def collect(name, key, ttl, load, select):
            try:
                value = await self._cached(key, ttl, load)
                if value is not None:
                    brief[name] = select(value)
            except Exception:
                pass
            if brief[name] is None:
                brief["warnings"].append(name + "_unavailable")

        tasks = [
            collect("daily", f"daily:{position}:7", TTLCache.TTL_DAILY, load_daily,
                    lambda days: next((d.to_dict() for d in days if d.date == day.isoformat()), None)),
            collect("hourly", f"hourly:{position}:24:{loc.tz}", TTLCache.TTL_HOURLY, load_hourly,
                    lambda hours: summarize_hours(hours, start, end, tz)),
        ]
        # The three-day index product only covers today and the next two days.
        if 0 <= (day - now_local.date()).days <= 2:
            tasks.append(collect(
                "indices", f"indices:{loc.id}:{now_local.date()}", 3600,
                lambda: self.client.get_indices(loc.id),
                lambda data: [
                    {key: item.get(key) for key in ("date", "type", "name", "level", "category", "text")}
                    for item in data["daily"] if item.get("date") == day.isoformat()
                    and str(item.get("type")) in {"1", "2", "3", "5"}
                    and (item.get("category") or item.get("text"))
                ] or None,
            ))
        else:
            brief["warnings"].append("indices_out_of_range")

        if today:
            tasks.extend([
                collect("current", f"current:{position}", TTLCache.TTL_CURRENT, load_current,
                        lambda current: current.to_dict()),
                collect("air_quality", f"air:{position}", 600,
                        lambda: self.client.get_air_quality(loc.lat, loc.lon),
                        _air_indexes),
                collect("alerts", f"alerts:{loc.id}", 300,
                        lambda: self.client.get_warnings(loc.id),
                        lambda data: _active_alerts(data, tz, now_local)),
            ])
        await asyncio.gather(*tasks)
        brief["warnings"].sort()
        return brief
