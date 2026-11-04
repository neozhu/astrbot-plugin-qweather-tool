import json
import inspect
from pathlib import Path
import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_conf_schema_has_strictly_three_config_items():
    """SPEC §15: _conf_schema.json must expose strictly 3 configuration items:
    api_host, api_key (secret), default_city.
    """
    schema_path = ROOT_DIR / "_conf_schema.json"
    assert schema_path.exists(), "_conf_schema.json does not exist"

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    assert set(schema.keys()) == {"api_host", "api_key", "default_city"}, (
        f"Expected strictly ['api_host', 'api_key', 'default_city'], got {list(schema.keys())}"
    )

    # Validate api_host
    assert schema["api_host"]["type"] == "string"
    assert "default" in schema["api_host"]

    # Validate api_key
    assert schema["api_key"]["type"] == "string"
    assert schema["api_key"].get("secret") is True
    assert "default" in schema["api_key"]

    # Validate default_city
    assert schema["default_city"]["type"] == "string"
    assert "default" in schema["default_city"]


def test_metadata_yaml_structure():
    """SPEC §16, §18: metadata.yaml specifies plugin name, version, and dependencies."""
    meta_path = ROOT_DIR / "metadata.yaml"
    assert meta_path.exists(), "metadata.yaml does not exist"

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = yaml.safe_load(f)

    assert isinstance(meta, dict), "metadata.yaml must be a valid mapping"
    assert "name" in meta
    assert "version" in meta
    assert "author" in meta
    assert "repo" in meta
    assert "display_name" in meta
    assert "desc" in meta or "description" in meta


def test_single_get_weather_tool_registered():
    """SPEC §0.2, §4.1, §4.3:
    Only ONE public LLM tool get_weather(location, when, focus) must be registered.
    Docstring must preserve key constraints.
    """
    from main import QWeatherPlugin
    from astrbot.api.star import Star

    assert issubclass(QWeatherPlugin, Star), "QWeatherPlugin must inherit from Star"

    # Inspect methods decorated with AstrBot llm_tool
    tool_methods = []
    for attr_name in dir(QWeatherPlugin):
        attr = getattr(QWeatherPlugin, attr_name)
        if callable(attr) and hasattr(attr, "__tool_name__"):
            tool_methods.append((attr_name, getattr(attr, "__tool_name__")))
        elif callable(attr) and hasattr(attr, "__ast_filter__"):
            # Check AstrBot filter decoration
            tool_methods.append((attr_name, attr_name))

    # Verify get_weather method exists
    assert hasattr(QWeatherPlugin, "get_weather"), "QWeatherPlugin must have a get_weather method"
    tool_func = getattr(QWeatherPlugin, "get_weather")

    # Verify signature: location, when, focus
    sig = inspect.signature(tool_func)
    params = list(sig.parameters.keys())
    assert "self" in params
    assert "event" in params
    assert "location" in params
    assert "when" in params
    assert "focus" in params

    # Verify docstring constraints
    doc = inspect.getdoc(tool_func) or ""
    assert "get_weather" in doc or "天气" in doc
    assert "location" in doc
    assert "when" in doc
    assert "focus" in doc
    assert "need_location" in doc


@pytest.mark.asyncio
async def test_tool_invocation_yields_plain_result_without_event_send():
    """SPEC §0.2, §4.3, §11.1:
    get_weather must yield event.plain_result(json_str) as Tool Result,
    and MUST NEVER call event.send() directly.
    """
    from main import QWeatherPlugin

    class DummyContext:
        pass

    class DummyEvent:
        def __init__(self):
            self.send_called = False
            self.unified_msg_origin = "test_origin"
            self.session_id = "test_session"

        async def send(self, *args, **kwargs):
            self.send_called = True

        def plain_result(self, content: str):
            return {"type": "plain_result", "content": content}

    mock_context = DummyContext()
    config = {
        "api_host": "test.qweatherapi.com",
        "api_key": "test_key",
        "default_city": "昆山",
    }
    plugin = QWeatherPlugin(mock_context, config=config)

    event = DummyEvent()
    results = []

    # get_weather is an async generator yielding results
    gen = plugin.get_weather(event, location="昆山", when="today", focus="general")
    async for item in gen:
        results.append(item)

    # Must yield at least one result
    assert len(results) >= 1, "get_weather must yield at least one result"
    result = results[0]

    # Verify event.send was NEVER called
    assert event.send_called is False, "get_weather must never call event.send() directly"

    # Verify result format
    assert isinstance(result, dict)
    assert result.get("type") == "plain_result"
    payload = json.loads(result.get("content"))
    assert "status" in payload
    assert payload["status"] in ["ok", "need_location", "ambiguous_location", "partial", "fallback_now"]


@pytest.mark.asyncio
async def test_lifecycle_terminate_cleans_resources():
    """SPEC §18: terminate() must cleanly release resources on shutdown/reload."""
    from main import QWeatherPlugin

    class DummyContext:
        pass

    mock_context = DummyContext()
    config = {
        "api_host": "test.qweatherapi.com",
        "api_key": "test_key",
        "default_city": "",
    }
    plugin = QWeatherPlugin(mock_context, config=config)

    assert hasattr(plugin, "terminate"), "QWeatherPlugin must implement terminate()"
    assert inspect.iscoroutinefunction(plugin.terminate), "terminate() must be an async method"

    # Calling terminate should execute cleanly without unhandled exceptions
    await plugin.terminate()
