import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class Event:
    def __init__(self, platform="wechat_native"):
        self.platform = platform
        self.extras = {}
        self.unified_msg_origin = f"{platform}:FriendMessage:test"
        self.result = None

    def get_platform_name(self):
        return self.platform

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_result(self):
        return self.result


@pytest.fixture
def plugin():
    from main import QWeatherPlugin

    instance = QWeatherPlugin(object())
    instance.weather_service.query = AsyncMock(return_value={"status": "ok"})
    return instance


@pytest.mark.parametrize("text,expected", [
    ("***注意保暖***", "注意保暖"),
    ("**昆山**\n****\n-2°C，降雨概率 0%", "昆山\n\n-2°C，降雨概率 0%"),
    ("2*3*4；2 * 3 * 4；weather_current", "2*3*4；2 * 3 * 4；weather_current"),
    ("```text\n气温 0°C\n```", "气温 0°C\n"),
])
def test_plain_text_preserves_values_and_literal_symbols(text, expected):
    from main import strip_weather_markdown

    assert strip_weather_markdown(text) == expected


@pytest.mark.asyncio
async def test_wechat_weather_result_requests_plain_text(plugin):
    payload = json.loads(await plugin.get_weather(Event(), "昆山", "now", "general"))
    assert payload["status"] == "ok"
    assert "纯文本" in payload["response_guidance"]


@pytest.mark.asyncio
async def test_weather_reply_removes_markdown_and_preserves_message_components(plugin):
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import At, Plain

    event = Event()
    await plugin.get_weather(event, "昆山", "now", "general")
    mention = At(qq="123", name="朋友")
    event.result = MessageEventResult(chain=[
        mention,
        Plain("### **昆山天气**\n- **气温**：16～20°C\n- 体感 `18°C`，风速 3.2m/s\n建议：__带伞__，*薄外套*。"),
    ])
    await plugin.format_wechat_weather_reply(event)
    assert event.result.chain[0] is mention
    assert event.result.chain[1].text == "昆山天气\n• 气温：16～20°C\n• 体感 18°C，风速 3.2m/s\n建议：带伞，薄外套。"


@pytest.mark.asyncio
async def test_weather_direct_send_tool_is_also_plain_text(plugin):
    event = Event()
    await plugin.get_weather(event, "昆山", "today", "rain")
    image = {"type": "image", "path": "/tmp/weather.png"}
    args = {"session": "", "messages": [{"type": "plain", "text": "**今天有雨**，降雨概率 60%。"}, image]}
    await plugin.format_wechat_weather_tool_message(event, SimpleNamespace(name="send_message_to_user"), args)
    assert args["messages"][0]["text"] == "今天有雨，降雨概率 60%。"
    assert args["messages"][1] is image


@pytest.mark.asyncio
async def test_wechat_cached_answer_is_plain_text_without_a_tool_call(plugin):
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import Plain

    event = Event()
    event.result = MessageEventResult(chain=[Plain("昆山今天预报**少云**，气温约 **13～21℃**。")])
    await plugin.format_wechat_weather_reply(event)
    assert event.result.chain[0].text == "昆山今天预报少云，气温约 13～21℃。"
    args = {"messages": [{"type": "plain", "text": "**少云**"}]}
    await plugin.format_wechat_weather_tool_message(event, SimpleNamespace(name="send_message_to_user"), args)
    assert args["messages"][0]["text"] == "少云"


@pytest.mark.asyncio
async def test_wechat_request_guides_current_weather_and_hourly_probability(plugin):
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.api.provider import ProviderRequest

    request = ProviderRequest(system_prompt="原有人格", func_tool=ToolSet([
        FunctionTool(name="get_weather", description="天气", parameters={"type": "object"})
    ]))
    await plugin.guide_wechat_weather_request(Event(), request)
    assert request.system_prompt.startswith("原有人格")
    assert "纯文本" in request.system_prompt
    assert "when=now" in request.system_prompt
    assert "小时预报" in request.system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["wechat_native", "webchat", "telegram"])
async def test_weather_tool_is_preferred_over_search_on_every_platform(plugin, platform):
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.api.provider import ProviderRequest

    tools = ToolSet([
        FunctionTool(name="get_weather", description="天气", parameters={"type": "object"}),
        FunctionTool(name="web_search_tavily", description="搜索", parameters={"type": "object"}),
    ])
    request = ProviderRequest(system_prompt="原有人格", func_tool=tools)
    event = Event(platform)
    await plugin.guide_wechat_weather_request(event, request)

    assert request.system_prompt.startswith("原有人格")
    assert "优先调用 get_weather" in request.system_prompt
    assert "联网搜索" in request.system_prompt
    assert "need_location" in request.system_prompt
    assert ("微信不支持 Markdown" in request.system_prompt) == (platform == "wechat_native")
    assert request.func_tool is tools
    assert tools.get_tool("web_search_tavily") is not None
    plugin.weather_service.query.assert_not_awaited()

    first_prompt = request.system_prompt
    await plugin.guide_wechat_weather_request(event, request)
    assert request.system_prompt == first_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["wechat_native", "webchat"])
@pytest.mark.parametrize("tool_names", [None, [], ["web_search_tavily"]])
async def test_unavailable_weather_tool_does_not_get_selection_guidance(plugin, platform, tool_names):
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.api.provider import ProviderRequest

    tools = None if tool_names is None else ToolSet([
        FunctionTool(name=name, description=name, parameters={"type": "object"})
        for name in tool_names
    ])
    request = ProviderRequest(system_prompt="原有人格", func_tool=tools)
    await plugin.guide_wechat_weather_request(Event(platform), request)
    assert "get_weather" not in request.system_prompt
    assert request.func_tool is tools
    if platform == "webchat":
        assert request.system_prompt == "原有人格"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,weather_query", [("telegram", False), ("telegram", True)])
async def test_other_replies_keep_their_format(plugin, platform, weather_query):
    from astrbot.api.event import MessageEventResult
    from astrbot.api.message_components import Plain

    event = Event(platform)
    if weather_query:
        payload = json.loads(await plugin.get_weather(event, "昆山", "now", "general"))
        assert "response_guidance" in payload  # style guidance now applies to weather on every platform
    event.result = MessageEventResult(chain=[Plain("**保留原文**")])
    args = {"messages": [{"type": "plain", "text": "**保留原文**"}]}
    await plugin.format_wechat_weather_reply(event)
    await plugin.format_wechat_weather_tool_message(event, SimpleNamespace(name="send_message_to_user"), args)
    assert event.result.chain[0].text == "**保留原文**"
    assert args["messages"][0]["text"] == "**保留原文**"


@pytest.mark.asyncio
async def test_other_tool_arguments_and_other_sessions_are_untouched(plugin):
    event = Event()
    await plugin.get_weather(event, "昆山", "now", "general")
    for name, session in [("other_tool", ""), ("send_message_to_user", "telegram:FriendMessage:other")]:
        args = {"session": session, "messages": [{"type": "plain", "text": "**保留原文**"}]}
        await plugin.format_wechat_weather_tool_message(event, SimpleNamespace(name=name), args)
        assert args["messages"][0]["text"] == "**保留原文**"
