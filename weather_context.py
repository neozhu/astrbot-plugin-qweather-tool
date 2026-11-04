from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    from .location_resolver import ResolvedLocation
    from .time_resolver import TargetPeriod
except (ImportError, ValueError):
    from location_resolver import ResolvedLocation
    from time_resolver import TargetPeriod

# SPEC §5.2: confirmed location TTL 7 days, topic context TTL 60 minutes
LOCATION_TTL_SECONDS = 7 * 24 * 3600
TOPIC_TTL_SECONDS = 60 * 60
# SPEC §5.3: pending weather request TTL 10 minutes
PENDING_TTL_SECONDS = 10 * 60


@dataclass
class PendingWeatherRequest:
    """Pending weather request waiting for location clarification (SPEC §5.3)."""
    kind: str  # "need_location" | "ambiguous_location"
    requester_id: str
    scope_key: str
    requested_focus: str
    requested_when: str
    anchor_utc: datetime
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


@dataclass
class WeatherContext:
    """Per-conversation weather context state (SPEC §5.2)."""
    confirmed_location: Optional[ResolvedLocation] = None
    confirmed_location_at: Optional[datetime] = None
    last_period: Optional[TargetPeriod] = None
    last_when_text: Optional[str] = None
    last_focus: Optional[str] = None
    last_success_at: Optional[datetime] = None
    pending: Optional[PendingWeatherRequest] = None

    def is_location_valid(self, now: datetime) -> bool:
        if not self.confirmed_location or not self.confirmed_location_at:
            return False
        return (now - self.confirmed_location_at).total_seconds() < LOCATION_TTL_SECONDS

    def get_valid_location(self, now: datetime) -> Optional[ResolvedLocation]:
        if self.is_location_valid(now):
            return self.confirmed_location
        return None

    def is_topic_valid(self, now: datetime) -> bool:
        if not self.last_success_at:
            return False
        return (now - self.last_success_at).total_seconds() < TOPIC_TTL_SECONDS

    def get_valid_focus(self, now: datetime) -> Optional[str]:
        if self.is_topic_valid(now):
            return self.last_focus
        return None

    def get_valid_when(self, now: datetime) -> Optional[str]:
        if self.is_topic_valid(now):
            return self.last_when_text
        return None

    def set_confirmed_location(self, loc: ResolvedLocation, now: datetime) -> None:
        self.confirmed_location = loc
        self.confirmed_location_at = now

    def update_topic(
        self,
        focus: str,
        period: TargetPeriod,
        when_text: str,
        now: datetime,
    ) -> None:
        self.last_focus = focus
        self.last_period = period
        self.last_when_text = when_text
        self.last_success_at = now

    def clear_pending(self) -> None:
        self.pending = None


class WeatherContextStore:
    """Manages WeatherContext for all active conversation scopes."""

    def __init__(self):
        self._contexts: Dict[str, WeatherContext] = {}

    @staticmethod
    def extract_scope_key(event: Any) -> str:
        """Derive conversation scope key conforming to SPEC §5.1: (platform_id, origin, session_id)."""
        platform_id = getattr(event, "platform_id", "default")
        origin = getattr(event, "unified_msg_origin", "")

        session_id = None
        if hasattr(event, "session_id") and event.session_id:
            session_id = str(event.session_id)
        elif hasattr(event, "message_obj") and getattr(event.message_obj, "session_id", None):
            session_id = str(getattr(event.message_obj, "session_id"))
        elif origin:
            session_id = str(origin)
        else:
            session_id = "default_session"

        return f"{platform_id}:{origin}:{session_id}"

    @staticmethod
    def extract_requester_id(event: Any) -> str:
        """Extract stable sender ID conforming to SPEC §5.3."""
        if hasattr(event, "message_obj"):
            msg_obj = getattr(event, "message_obj")
            sender = getattr(msg_obj, "sender", None)
            if sender:
                user_id = getattr(sender, "user_id", None)
                if user_id:
                    return str(user_id)
        if hasattr(event, "sender_id") and event.sender_id:
            return str(event.sender_id)
        return "unknown_sender"

    def get_context(self, scope_key: str) -> WeatherContext:
        if scope_key not in self._contexts:
            self._contexts[scope_key] = WeatherContext()
        return self._contexts[scope_key]

    def save_pending(
        self,
        event: Any,
        kind: str,
        requested_focus: str,
        requested_when: str,
        anchor_utc: datetime,
    ) -> None:
        scope_key = self.extract_scope_key(event)
        requester_id = self.extract_requester_id(event)
        ctx = self.get_context(scope_key)

        ctx.pending = PendingWeatherRequest(
            kind=kind,
            requester_id=requester_id,
            scope_key=scope_key,
            requested_focus=requested_focus,
            requested_when=requested_when or "now",
            anchor_utc=anchor_utc,
            created_at=anchor_utc,
            expires_at=anchor_utc + timedelta(seconds=PENDING_TTL_SECONDS),
        )

    def get_resumable_pending(
        self,
        event: Any,
        is_group: bool,
        now: datetime,
    ) -> Optional[PendingWeatherRequest]:
        scope_key = self.extract_scope_key(event)
        if scope_key not in self._contexts:
            return None
        ctx = self._contexts[scope_key]
        if not ctx.pending:
            return None

        if ctx.pending.is_expired(now):
            ctx.pending = None
            return None

        # SPEC §5.3 #3: Group chat Pending is strictly isolated by requester_id
        if is_group:
            requester_id = self.extract_requester_id(event)
            if requester_id != ctx.pending.requester_id:
                return None

        return ctx.pending
