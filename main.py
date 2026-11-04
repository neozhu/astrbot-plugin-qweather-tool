import json
from typing import Any, Dict

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from weather_service import WeatherService


@register(
    "astrbot_plugin_qweather_tool",
    "neozhu",
    "A context-aware weather tool for AstrBot LLM agents powered by QWeather.",
    "0.1.0",
)
class QWeatherPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any] = None):
        super().__init__(context)
        self.config = config or {}
        self.weather_service = WeatherService(
            api_host=self.config.get("api_host", ""),
            api_key=self.config.get("api_key", ""),
            default_city=self.config.get("default_city", ""),
        )

    @filter.llm_tool(name="get_weather")
    async def get_weather(
        self,
        event: AstrMessageEvent,
        location: str,
        when: str,
        focus: str,
    ):
        """为自然聊天提供真实天气事实，最终回答由当前 AstrBot LLM 生成。

        用户明确查天气、需要温差比较、询问依赖天气的穿衣/户外/洗车建议，
        或在有效天气话题内继续追问且需要新数据时调用。
        普通寒暄、情绪表达、气象科普和无关对话不调用。
        当前上下文已有足够新、足够细的可信数据时无需重复调用。

        只修改用户本轮明确修改的地点、日期、时段和意图；优先继承有效
        天气会话或 Pending，而不是把每次省略日期都当作“现在”。
        地点无法确定则 location 留空，本工具返回 need_location；
        你应自然询问城市，用户补充后继续原问题。

        将自然表达归一化为约定的短参数，不要直接传入完整用户问句。
        不负责计算当地绝对日期、不猜测时区、不生成经纬度。
        完全没有时间线索时 when 传空字符串；若确有明确但无法
        归一化的时间短语，应传该短语原文（仅时间部分），以便插件标记
        unrecognized_explicit_time 并默认 now；不要直接丢失该时间线索。
        不要把当前天气冒充用户明确提过的未来时段预报。

        Args:
            location(string): 城市/行政区，如昆山、上海；未知时传空字符串。
            when(string): 优先 now, today, yesterday, tomorrow, +Nd, -Nd,
                YYYY-MM-DD；可加 @morning/@forenoon/@afternoon/
                @evening/@night/@HH:MM；对比用 "目标 vs 基准"，
                如 "tomorrow@morning vs today@morning"；跨时段用
                "today@evening to tomorrow@morning"。未给出时间传空字符串；
                明确但无法规范化的时间仅传其原始短语，插件回退 now。
            focus(string): 仅使用 general, rain, clothing, comparison,
                outdoor, car_wash 六个值；不要传完整用户问句；
                current 不是 focus 值，表示现在请使用 when=now。
        """
        result = await self.weather_service.query(
            event=event,
            location_text=location,
            when_text=when,
            focus_text=focus,
        )
        yield event.plain_result(json.dumps(result, ensure_ascii=False))

    async def terminate(self):
        """Cleanly release resources on plugin shutdown or reload."""
        if hasattr(self, "weather_service") and self.weather_service:
            await self.weather_service.close()
