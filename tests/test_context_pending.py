from datetime import datetime, timezone
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


@pytest.mark.asyncio
async def test_pending_saved_and_resumed_c04_c62(mock_kunshan):
    """SPEC §5.3, §5.4, C04, C62:
    Turn 1: user asks '明天穿什么？' (location='', when='tomorrow', focus='clothing')
            -> returns status='need_location', saves pending.
    Turn 2: user replies '昆山' (location='昆山', when='', focus='')
            -> resumes pending clothing question for tomorrow in Kunshan.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event = MockAstrEvent(origin="private_u1", session_id="s1", sender_id="u1")

    # Turn 1
    t1 = await service.query(event, location_text="", when_text="tomorrow", focus_text="clothing")
    assert t1["status"] == "need_location"
    assert t1.get("pending_saved") is True

    # Turn 2
    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])
        t2 = await service.query(event, location_text="昆山", when_text="", focus_text="")

        assert t2["status"] == "ok"
        assert t2["location"]["name"] == "昆山"
        assert t2["query"]["focus"] == "clothing"
        assert "tomorrow" in t2["query"]["normalized_when"]


@pytest.mark.asyncio
async def test_pending_midnight_anchor_stability_c41_c62(mock_kunshan):
    """SPEC §5.3 #5, C41, C62:
    User asks '明天穿什么' at 23:59:00 (anchor_utc = 2026-11-04 15:59:00 UTC = 23:59 Shanghai).
    User replies '昆山' at 00:01:00 on Nov 5.
    Relative 'tomorrow' must resolve to 2026-11-05 using anchor_utc, NOT 2026-11-06.
    """
    from weather_service import WeatherService

    # Fixed original time: 2026-11-04 23:59:00 Shanghai (15:59:00 UTC)
    t_orig = datetime(2026, 11, 4, 15, 59, 0, tzinfo=timezone.utc)
    # Followup time: 2026-11-05 00:01:00 Shanghai (16:01:00 UTC)
    t_after_midnight = datetime(2026, 11, 4, 16, 1, 0, tzinfo=timezone.utc)

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    service.time_resolver.clock = lambda: t_orig
    event = MockAstrEvent(origin="private_u1", session_id="s1", sender_id="u1")

    # Turn 1 at 23:59
    await service.query(event, location_text="", when_text="tomorrow", focus_text="clothing")

    # Turn 2 at 00:01 (after midnight)
    service.time_resolver.clock = lambda: t_after_midnight
    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])
        t2 = await service.query(event, location_text="昆山", when_text="", focus_text="")

        assert t2["status"] == "ok"
        # Must resolve to 2026-11-05, not 2026-11-06!
        assert "2026-11-05" in t2["query"]["target_start_local"]


@pytest.mark.asyncio
async def test_group_pending_requester_isolation_c28_c29(mock_kunshan, mock_suzhou):
    """SPEC §5.3 #3, C28, C29:
    In group chat, Pending is isolated by sender ID.
    User A asks without location -> Pending saved for A.
    User B says '上海' -> B CANNOT hijack A's pending.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event_a = MockAstrEvent(origin="group_1", session_id="g1_session", sender_id="user_A", is_group=True)
    event_b = MockAstrEvent(origin="group_1", session_id="g1_session", sender_id="user_B", is_group=True)

    # User A initiates need_location
    t_a = await service.query(event_a, location_text="", when_text="tomorrow", focus_text="clothing")
    assert t_a["status"] == "need_location"

    # User B enters city '苏州'
    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_suzhou, candidates=[])
        t_b = await service.query(event_b, location_text="苏州", when_text="", focus_text="")

        # User B did not inherit User A's pending clothing query
        assert t_b["status"] == "ok"
        assert t_b["query"]["focus"] == "general"  # not 'clothing'
        assert t_b["query"]["time_resolution"]["status"] == "fallback_now"


@pytest.mark.asyncio
async def test_multiturn_context_inheritance_c05_c06_c07(mock_kunshan, mock_suzhou):
    """SPEC §5.2, C05, C06, C07:
    Turn 1: '昆山明天会下雨吗？' -> (location=昆山, when=tomorrow, focus=rain)
    Turn 2: '那下午呢？' -> inherits 昆山 and rain, updates to tomorrow@afternoon
    Turn 3: '那带伞吗？' -> inherits 昆山 and tomorrow@afternoon
    Turn 4: '换成苏州呢？' -> updates to 苏州, inherits afternoon and rain
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event = MockAstrEvent(origin="private_u2", session_id="s2", sender_id="u2")

    # Turn 1
    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])
        t1 = await service.query(event, location_text="昆山", when_text="tomorrow", focus_text="rain")
        assert t1["status"] == "ok"
        assert t1["location"]["name"] == "昆山"
        assert t1["query"]["focus"] == "rain"

    # Turn 2: '那下午呢？' (location="", when="tomorrow@afternoon", focus="")
    t2 = await service.query(event, location_text="", when_text="tomorrow@afternoon", focus_text="")
    assert t2["status"] == "ok"
    assert t2["location"]["name"] == "昆山"
    assert t2["query"]["focus"] == "rain"
    assert "afternoon" in t2["query"]["normalized_when"]

    # Turn 3: '那带伞吗？' (location="", when="", focus="rain")
    t3 = await service.query(event, location_text="", when_text="", focus_text="rain")
    assert t3["status"] == "ok"
    assert t3["location"]["name"] == "昆山"
    assert "afternoon" in t3["query"]["normalized_when"]

    # Turn 4: '换成苏州呢？' (location="苏州", when="", focus="")
    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_suzhou, candidates=[])
        t4 = await service.query(event, location_text="苏州", when_text="", focus_text="")
        assert t4["status"] == "ok"
        assert t4["location"]["name"] == "苏州"
        assert t4["query"]["focus"] == "rain"
        assert "afternoon" in t4["query"]["normalized_when"]


@pytest.mark.asyncio
async def test_session_switching_isolation_c30_c38(mock_kunshan):
    """SPEC §5.1, C30, C38:
    Session G1 confirmed Kunshan.
    Session G2 asks '那下午呢' with empty location -> G2 cannot borrow G1's location.
    """
    from weather_service import WeatherService

    service = WeatherService(api_host="https://test.qweatherapi.com", api_key="test_key")
    event_g1 = MockAstrEvent(origin="group_1", session_id="g1_session", sender_id="u1", is_group=True)
    event_g2 = MockAstrEvent(origin="group_2", session_id="g2_session", sender_id="u2", is_group=True)

    # G1 queries Kunshan
    with patch.object(service.location_resolver, "resolve") as mock_loc:
        mock_loc.return_value = AsyncMock(status="ok", location=mock_kunshan, candidates=[])
        await service.query(event_g1, location_text="昆山", when_text="tomorrow", focus_text="general")

    # G2 queries with empty location
    t_g2 = await service.query(event_g2, location_text="", when_text="tomorrow@afternoon", focus_text="general")
    assert t_g2["status"] == "need_location"
