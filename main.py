import asyncio
import inspect
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict

# Ensure plugin directory is in sys.path so intra-plugin imports always resolve
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import aiohttp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .weather_service import WeatherService
except (ImportError, ValueError):
    from weather_service import WeatherService

# AstrBot Web API & Plugin Views support
try:
    from astrbot.api.web import Response, error_response, json_response, request
except ImportError:
    try:
        from quart import Response, request

        def json_response(data, status_code=200):
            return Response(
                json.dumps(data, ensure_ascii=False),
                mimetype="application/json",
                status=status_code,
            )

        def error_response(msg, status_code=400):
            return Response(
                json.dumps({"code": status_code, "error": msg, "message": msg}, ensure_ascii=False),
                mimetype="application/json",
                status=status_code,
            )
    except ImportError:
        class Response:
            def __init__(self, body, mimetype="text/html", status=200):
                self.body = body
                self.mimetype = mimetype
                self.status = status

        def json_response(data, status_code=200):
            return json.dumps(data, ensure_ascii=False), status_code

        def error_response(msg, status_code=400):
            return json.dumps({"error": msg}, ensure_ascii=False), status_code

        request = None

PLUGIN_NAME = "astrbot_plugin_qweather_tool"


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

        # Register Web API endpoints for AstrBot WebUI & Plugin Views
        if hasattr(context, "register_web_api"):
            try:
                context.register_web_api(
                    f"/{PLUGIN_NAME}/test-connection",
                    self.handle_test_connection,
                    ["GET", "POST"],
                    "测试和风天气 API 连通性",
                )
                context.register_web_api(
                    f"/{PLUGIN_NAME}/test-query",
                    self.handle_test_query,
                    ["GET", "POST"],
                    "测试和风天气查询",
                )
                context.register_web_api(
                    f"/{PLUGIN_NAME}/status",
                    self.handle_status,
                    ["GET"],
                    "获取插件配置与状态",
                )
                context.register_web_api(
                    f"/{PLUGIN_NAME}/test",
                    self.handle_view_test,
                    ["GET"],
                    "和风天气连通性测试与调试页面",
                )
            except Exception:
                pass

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

    async def _parse_request_body(self) -> Dict[str, Any]:
        """Safely parse JSON or query params from AstrBot or Quart request."""
        if request is None:
            return {}
        data: Dict[str, Any] = {}
        # Try JSON body first
        try:
            if hasattr(request, "get_json"):
                res = request.get_json()
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, dict):
                    data.update(res)
            elif hasattr(request, "json"):
                if callable(request.json):
                    res = request.json()
                    if inspect.isawaitable(res):
                        res = await res
                    if isinstance(res, dict):
                        data.update(res)
                elif isinstance(request.json, dict):
                    data.update(request.json)
        except Exception:
            pass

        # Also merge query parameters
        try:
            query = getattr(request, "args", None) or getattr(request, "query", None)
            if query:
                for k, v in query.items():
                    if k not in data or not data[k]:
                        data[k] = v
        except Exception:
            pass
        return data

    async def handle_status(self, *args, **kwargs):
        """Return current configuration status (with masked key)."""
        api_key = self.config.get("api_key") or ""
        masked = ""
        if api_key:
            if len(api_key) > 8:
                masked = api_key[:4] + "********" + api_key[-4:]
            else:
                masked = "********"
        return json_response({
            "plugin": PLUGIN_NAME,
            "version": "0.1.0",
            "api_host": self.config.get("api_host") or "devapi.qweather.com",
            "has_api_key": bool(api_key),
            "masked_api_key": masked,
            "default_city": self.config.get("default_city") or "北京",
        })

    async def handle_view_test(self, *args, **kwargs):
        """Return views/test/index.html directly for web access."""
        html_path = Path(__file__).resolve().parent / "views" / "test" / "index.html"
        if html_path.exists():
            content = html_path.read_text(encoding="utf-8")
            return Response(content, mimetype="text/html")
        return json_response({"error": "View file not found"}, status_code=404)

    async def handle_test_connection(self, *args, **kwargs):
        """Test connectivity against QWeather API using current or provided credentials."""
        req_data = await self._parse_request_body()
        api_host = req_data.get("api_host") or self.config.get("api_host") or "devapi.qweather.com"
        api_key = req_data.get("api_key") or self.config.get("api_key") or ""

        if not api_key:
            return json_response({
                "success": False,
                "code": "MISSING_API_KEY",
                "message": "未配置 API Key。请在 AstrBot 控制台填写或在上方临时测试框输入。",
                "api_host": api_host,
            })

        clean_host = api_host.replace("https://", "").replace("http://", "").strip("/")
        test_url = f"https://{clean_host}/v2/city/lookup"

        start_time = time.time()
        try:
            timeout = aiohttp.ClientTimeout(total=6.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {"X-QW-Api-Key": api_key}
                test_city = req_data.get("city") or self.config.get("default_city") or "北京"
                params = {"location": test_city}
                async with session.get(test_url, headers=headers, params=params) as resp:
                    latency_ms = int((time.time() - start_time) * 1000)
                    resp_status = resp.status
                    try:
                        resp_data = await resp.json()
                    except Exception:
                        resp_data = {}

                    code = str(resp_data.get("code", resp_status))
                    if resp_status == 200 and code == "200":
                        locations = resp_data.get("location", [])
                        loc_name = locations[0].get("name", test_city) if locations else test_city
                        adm1 = locations[0].get("adm1", "") if locations else ""
                        return json_response({
                            "success": True,
                            "code": "200",
                            "latency_ms": latency_ms,
                            "message": f"连接成功！QWeather GeoAPI 鉴权与网络正常 (耗时 {latency_ms}ms)。",
                            "api_host": clean_host,
                            "resolved_city": f"{loc_name} ({adm1})" if adm1 else loc_name,
                        })
                    elif resp_status == 401 or code == "401":
                        return json_response({
                            "success": False,
                            "code": "401",
                            "latency_ms": latency_ms,
                            "message": "认证失败 (401 Unauthorized)：API Key 无效，请核对是否正确填写。",
                            "api_host": clean_host,
                        })
                    elif resp_status == 402 or code == "402":
                        return json_response({
                            "success": False,
                            "code": "402",
                            "latency_ms": latency_ms,
                            "message": "账户已欠费或配额已耗尽 (402 Payment Required)。",
                            "api_host": clean_host,
                        })
                    elif resp_status == 403 or code == "403":
                        return json_response({
                            "success": False,
                            "code": "403",
                            "latency_ms": latency_ms,
                            "message": f"无权限访问 (403 Forbidden)：请确认域名 {clean_host} 是否与您的 QWeather 项目凭证类型匹配（免费版凭证请用 devapi.qweather.com，商业版请用 api.qweather.com）。",
                            "api_host": clean_host,
                        })
                    else:
                        return json_response({
                            "success": False,
                            "code": code,
                            "latency_ms": latency_ms,
                            "message": f"QWeather API 异常：HTTP {resp_status}, 错误码 {code}。",
                            "api_host": clean_host,
                        })
        except aiohttp.ClientConnectorError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            return json_response({
                "success": False,
                "code": "CONNECTION_FAILED",
                "latency_ms": latency_ms,
                "message": f"无法连接到 {clean_host}：网络连接失败 ({e})。请检查服务器网络或 DNS。",
                "api_host": clean_host,
            })
        except asyncio.TimeoutError:
            return json_response({
                "success": False,
                "code": "TIMEOUT",
                "message": f"连接超时 (6s)：访问 {clean_host} 超时，请检查服务器网络环境或防火墙设置。",
                "api_host": clean_host,
            })
        except Exception as e:
            return json_response({
                "success": False,
                "code": "ERROR",
                "message": f"测试过程发生异常: {e}",
                "api_host": clean_host,
            })

    async def handle_test_query(self, *args, **kwargs):
        """Test weather parsing and querying workflow."""
        req_data = await self._parse_request_body()
        location = req_data.get("location") or self.config.get("default_city") or "北京"
        when = req_data.get("when") or "今天"
        focus = req_data.get("focus") or "general"

        try:
            result = await self.weather_service.query(
                event=None,
                location_text=location,
                when_text=when,
                focus_text=focus,
            )
            return json_response({
                "success": True,
                "result": result,
                "input": {
                    "location": location,
                    "when": when,
                    "focus": focus,
                },
            })
        except Exception as e:
            return json_response({
                "success": False,
                "error": str(e),
                "message": f"查询执行失败: {e}",
            })

    async def terminate(self):
        """Cleanly release resources on plugin shutdown or reload."""
        if hasattr(self, "weather_service") and self.weather_service:
            await self.weather_service.close()
