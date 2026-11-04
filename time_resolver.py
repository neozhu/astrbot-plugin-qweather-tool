from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo


@dataclass
class TargetPeriod:
    """Half-open local time window [start_local, end_local) and ISO strings."""
    start_local: str
    end_local: str
    granularity: str  # "instant", "hourly", "daily"
    semantic: str = ""


@dataclass
class TimeResolution:
    """Metadata regarding how the time parameter was resolved (SPEC §7.5)."""
    status: str  # "explicit", "fallback_now", "inherited_pending", "inherited_context"
    reason: Optional[str] = None  # "time_unspecified", "unrecognized_explicit_time", etc.
    input: str = ""
    timezone: str = "Asia/Shanghai"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "input": self.input,
            "timezone": self.timezone,
        }


@dataclass
class ResolvedTime:
    """Complete time resolution result with optional comparison baseline."""
    target: TargetPeriod
    baseline: Optional[TargetPeriod] = None
    resolution: TimeResolution = None  # type: ignore


class TimeResolver:
    """Resolves temporal expressions to local time intervals conforming to SPEC-v1.3 §7."""

    DAYPARTS = {
        "morning": (time(6, 0), time(9, 0)),
        "forenoon": (time(6, 0), time(12, 0)),
        "afternoon": (time(12, 0), time(18, 0)),
        "evening": (time(18, 0), time(23, 59, 59)),
        "night": (time(22, 0), time(6, 0)),  # crosses midnight
    }

    def __init__(self, clock: Optional[Callable[[], datetime]] = None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _replace_chinese_tokens(self, text: str) -> str:
        s = text.strip()
        # Chinese date phrases
        s = re.sub(r"大后天", "+3d", s)
        s = re.sub(r"后天", "+2d", s)
        s = re.sub(r"明天", "tomorrow", s)
        s = re.sub(r"今天", "today", s)
        s = re.sub(r"昨天", "yesterday", s)
        s = re.sub(r"现在|此刻", "now", s)

        # Chinese daypart / hour phrases
        s = re.sub(r"明早(\d{1,2})点", r"tomorrow@\1:00", s)
        s = re.sub(r"明早|早晨|早上", "@morning", s)
        s = re.sub(r"傍晚|晚上", "@evening", s)
        s = re.sub(r"下午", "@afternoon", s)
        s = re.sub(r"上午", "@forenoon", s)
        s = re.sub(r"夜间|深夜", "@night", s)
        s = re.sub(r"到", " to ", s)
        s = re.sub(r"对比|比", " vs ", s)

        # Formatting fix
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _parse_single_period(
        self,
        period_str: str,
        now_local: datetime,
        tz_obj: ZoneInfo,
    ) -> Optional[TargetPeriod]:
        p = period_str.strip()

        # Instant 'now'
        if p == "now":
            iso_now = now_local.isoformat()
            return TargetPeriod(
                start_local=iso_now,
                end_local=iso_now,
                granularity="instant",
                semantic="now",
            )

        # Parse date and optional daypart/time
        date_part = p
        part_or_time = None
        if "@" in p:
            parts = p.split("@", 1)
            date_part = parts[0].strip()
            part_or_time = parts[1].strip()

        # Determine target date
        base_date = now_local.date()
        if date_part == "today":
            target_date = base_date
        elif date_part == "tomorrow":
            target_date = base_date + timedelta(days=1)
        elif date_part == "yesterday":
            target_date = base_date - timedelta(days=1)
        elif re.match(r"^\+\d+d$", date_part):
            days_n = int(date_part[1:-1])
            target_date = base_date + timedelta(days=days_n)
        elif re.match(r"^-\d+d$", date_part):
            days_n = int(date_part[1:-1])
            target_date = base_date - timedelta(days=days_n)
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", date_part):
            try:
                target_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            except ValueError:
                return None
        elif "周末" in date_part:
            # Coming Saturday
            days_ahead = (5 - base_date.weekday()) % 7
            if days_ahead == 0 and now_local.weekday() == 5:
                days_ahead = 0
            target_date = base_date + timedelta(days=days_ahead)
        else:
            return None

        # If no time / daypart specified, it's full day (daily)
        if not part_or_time:
            dt_start = datetime.combine(target_date, time.min, tzinfo=tz_obj)
            dt_end = dt_start + timedelta(days=1)
            return TargetPeriod(
                start_local=dt_start.isoformat(),
                end_local=dt_end.isoformat(),
                granularity="daily",
                semantic=date_part,
            )

        # Daypart or specific hour (hourly)
        if part_or_time in self.DAYPARTS:
            t_start, t_end = self.DAYPARTS[part_or_time]
            dt_start = datetime.combine(target_date, t_start, tzinfo=tz_obj)
            if part_or_time == "evening":
                dt_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=tz_obj)
            elif part_or_time == "night":
                dt_end = datetime.combine(target_date + timedelta(days=1), t_end, tzinfo=tz_obj)
            else:
                dt_end = datetime.combine(target_date, t_end, tzinfo=tz_obj)

            return TargetPeriod(
                start_local=dt_start.isoformat(),
                end_local=dt_end.isoformat(),
                granularity="hourly",
                semantic=f"{date_part}@{part_or_time}",
            )

        # HH:MM hour window
        m_time = re.match(r"^(\d{1,2}):(\d{2})$", part_or_time)
        if m_time:
            h = int(m_time.group(1))
            m = int(m_time.group(2))
            dt_start = datetime.combine(target_date, time(h, m), tzinfo=tz_obj)
            dt_end = dt_start + timedelta(hours=1)
            return TargetPeriod(
                start_local=dt_start.isoformat(),
                end_local=dt_end.isoformat(),
                granularity="hourly",
                semantic=f"{date_part}@{part_or_time}",
            )

        return None

    def resolve(self, when_text: str, tz: str = "Asia/Shanghai") -> ResolvedTime:
        """Resolve when expression with target timezone."""
        try:
            tz_obj = ZoneInfo(tz)
        except Exception:
            tz_obj = ZoneInfo("Asia/Shanghai")

        now_utc = self.clock()
        now_local = now_utc.astimezone(tz_obj)
        clean_input = (when_text or "").strip()

        # Fallback now on empty
        if not clean_input:
            iso_now = now_local.isoformat()
            target = TargetPeriod(start_local=iso_now, end_local=iso_now, granularity="instant", semantic="now")
            return ResolvedTime(
                target=target,
                resolution=TimeResolution(status="fallback_now", reason="time_unspecified", input="", timezone=tz),
            )

        norm_text = self._replace_chinese_tokens(clean_input)

        # Check range: PERIOD to PERIOD
        if " to " in norm_text:
            parts = norm_text.split(" to ", 1)
            p1 = self._parse_single_period(parts[0], now_local, tz_obj)
            p2 = self._parse_single_period(parts[1], now_local, tz_obj)
            if p1 and p2:
                target = TargetPeriod(
                    start_local=p1.start_local,
                    end_local=p2.end_local,
                    granularity="hourly",
                    semantic=f"{p1.semantic}_to_{p2.semantic}",
                )
                return ResolvedTime(
                    target=target,
                    resolution=TimeResolution(status="explicit", input=clean_input, timezone=tz),
                )

        # Check comparison: PERIOD vs PERIOD
        if " vs " in norm_text:
            parts = norm_text.split(" vs ", 1)
            p_target = self._parse_single_period(parts[0], now_local, tz_obj)
            p_baseline = self._parse_single_period(parts[1], now_local, tz_obj)
            if p_target and p_baseline:
                return ResolvedTime(
                    target=p_target,
                    baseline=p_baseline,
                    resolution=TimeResolution(status="explicit", input=clean_input, timezone=tz),
                )

        # Single period
        p = self._parse_single_period(norm_text, now_local, tz_obj)
        if p:
            return ResolvedTime(
                target=p,
                resolution=TimeResolution(status="explicit", input=clean_input, timezone=tz),
            )

        # Unrecognized explicit time -> fallback to now (SPEC §7.4, C59)
        iso_now = now_local.isoformat()
        target = TargetPeriod(start_local=iso_now, end_local=iso_now, granularity="instant", semantic="now")
        return ResolvedTime(
            target=target,
            resolution=TimeResolution(
                status="fallback_now",
                reason="unrecognized_explicit_time",
                input=clean_input,
                timezone=tz,
            ),
        )
