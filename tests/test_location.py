import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def geo_kunshan_data():
    with open(FIXTURES_DIR / "geo_kunshan_ambiguous.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def geo_large_diff_data():
    with open(FIXTURES_DIR / "geo_rank_large_diff.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def geo_close_diff_data():
    with open(FIXTURES_DIR / "geo_rank_close_diff.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_exact_name_preferred_over_smaller_rank_c69(geo_kunshan_data):
    """SPEC §6.2.1, §6.2.2, C69:
    '昆山' search returns '昆山区' (rank 18) and '昆山' (rank 32).
    Exact name match '昆山' MUST be preferred over '昆山区' despite higher rank number.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    mock_client.search_city.return_value = geo_kunshan_data["location"]

    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="昆山")

    assert res.status == "ok"
    assert res.location is not None
    assert res.location.name == "昆山"
    assert res.location.id == "101190404"
    assert res.location.adm2 == "苏州市"
    assert res.location.adm1 == "江苏省"
    assert res.resolution_method in ["exact_name", "exact_match"]


@pytest.mark.asyncio
async def test_explicit_adm_hard_constraint_c70():
    """SPEC §6.2.1 rule 1, C70:
    '江苏苏州昆山' -> explicit province/city constraints filter candidates.
    A candidate from another province with smaller rank must never be chosen.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    mock_client.search_city.return_value = [
        {
            "name": "昆山",
            "id": "9999",
            "lat": "25.00",
            "lon": "115.00",
            "adm2": "其他市",
            "adm1": "其他省",
            "country": "中国",
            "tz": "Asia/Shanghai",
            "type": "city",
            "rank": "1",
        },
        {
            "name": "昆山",
            "id": "101190404",
            "lat": "31.3846",
            "lon": "120.9813",
            "adm2": "苏州市",
            "adm1": "江苏省",
            "country": "中国",
            "tz": "Asia/Shanghai",
            "type": "city",
            "rank": "32",
        },
    ]

    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="江苏苏州昆山")

    assert res.status == "ok"
    assert res.location.id == "101190404"
    assert res.location.adm1 == "江苏省"
    assert res.location.adm2 == "苏州市"


@pytest.mark.asyncio
async def test_confirmed_location_priority_c71():
    """SPEC §6.2.1 rule 2, C71:
    When session has recently confirmed location (e.g. 101190404),
    and user simply enters '昆山', prioritize reusing that LocationID.
    """
    from location_resolver import LocationResolver, ResolvedLocation

    mock_client = AsyncMock()
    mock_client.search_city.return_value = [
        {
            "name": "昆山",
            "id": "101190404",
            "adm1": "江苏省",
            "adm2": "苏州市",
            "country": "中国",
            "lat": "31.38",
            "lon": "120.98",
            "tz": "Asia/Shanghai",
            "type": "city",
            "rank": "32",
        },
        {
            "name": "昆山",
            "id": "200000000",
            "adm1": "陕西省",
            "adm2": "某市",
            "country": "中国",
            "lat": "34.00",
            "lon": "108.00",
            "tz": "Asia/Shanghai",
            "type": "city",
            "rank": "30",
        },
    ]

    confirmed = ResolvedLocation(
        id="101190404",
        name="昆山",
        adm1="江苏省",
        adm2="苏州市",
        country="中国",
        lat=31.3846,
        lon=120.9813,
        tz="Asia/Shanghai",
    )

    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="昆山", confirmed_location=confirmed)

    assert res.status == "ok"
    assert res.location.id == "101190404"
    assert res.resolution_method == "confirmed_context"


@pytest.mark.asyncio
async def test_rank_large_diff_auto_selected_c72(geo_large_diff_data):
    """SPEC §6.2.1, C72:
    Two identical names with no administrative context, ranks 20 vs 40.
    Rank difference 40 - 20 = 20 >= 15 threshold: auto-select rank 20 candidate.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    mock_client.search_city.return_value = geo_large_diff_data["location"]

    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="某地")

    assert res.status == "ok"
    assert res.location.id == "1001"
    assert res.location.rank == 20
    assert res.resolution_method == "rank_priority"


@pytest.mark.asyncio
async def test_rank_close_diff_triggers_ambiguous_location_c73(geo_close_diff_data):
    """SPEC §6.2.1, C73:
    Two identical names, ranks 20 vs 25 (difference 5 < 15).
    Must return ambiguous_location with max 3 candidates without guessing.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    mock_client.search_city.return_value = geo_close_diff_data["location"]

    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="朝阳")

    assert res.status == "ambiguous_location"
    assert res.location is None
    assert len(res.candidates) == 2
    assert res.candidates[0]["name"] == "朝阳"
    assert "北京市" in res.candidates[0]["adm1"]
    assert "辽宁省" in res.candidates[1]["adm1"]


@pytest.mark.asyncio
async def test_rank_missing_triggers_ambiguous_location_c74():
    """SPEC §6.2.1, C74:
    Two candidates with missing or unparseable rank:
    Must return ambiguous_location, not guess or pick first item.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    mock_client.search_city.return_value = [
        {
            "name": "同名地点",
            "id": "1",
            "adm1": "省A",
            "adm2": "市A",
            "country": "中国",
            "tz": "Asia/Shanghai",
            "rank": "invalid",
        },
        {
            "name": "同名地点",
            "id": "2",
            "adm1": "省B",
            "adm2": "市B",
            "country": "中国",
            "tz": "Asia/Shanghai",
            "rank": None,
        },
    ]

    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="同名地点")

    assert res.status == "ambiguous_location"
    assert res.location is None
    assert len(res.candidates) == 2


@pytest.mark.asyncio
async def test_default_city_in_private_chat_only_c02_c63(geo_kunshan_data):
    """SPEC §6.1, C02, C63:
    When location is empty:
    - In private chat: falls back to default_city.
    - In group chat: NEVER uses default_city, returns need_location.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    mock_client.search_city.return_value = geo_kunshan_data["location"]

    resolver = LocationResolver(mock_client)

    # Private chat with default_city
    res_private = await resolver.resolve(
        location_text="",
        default_city="昆山",
        is_group=False,
    )
    assert res_private.status == "ok"
    assert res_private.location.name == "昆山"

    # Group chat with default_city
    res_group = await resolver.resolve(
        location_text="",
        default_city="昆山",
        is_group=True,
    )
    assert res_group.status == "need_location"
    assert res_group.location is None


@pytest.mark.asyncio
async def test_empty_location_returns_need_location_c03():
    """SPEC §6.1, §11.2, C03:
    No location, no default_city -> need_location.
    """
    from location_resolver import LocationResolver

    mock_client = AsyncMock()
    resolver = LocationResolver(mock_client)
    res = await resolver.resolve(location_text="", default_city="", is_group=False)

    assert res.status == "need_location"
    assert res.location is None


def test_qweather_client_validates_https_and_headers():
    """SPEC §10.1:
    QWeatherClient must validate API Host to be HTTPS and send X-QW-Api-Key header.
    """
    from qweather_client import QWeatherClient

    # Insecure HTTP must raise ValueError
    with pytest.raises(ValueError, match="HTTPS"):
        QWeatherClient(api_host="http://insecure.qweatherapi.com", api_key="secret")

    client = QWeatherClient(api_host="https://abc.qweatherapi.com", api_key="test_key")
    headers = client._build_headers()
    assert headers.get("X-QW-Api-Key") == "test_key"
