# astrbot-plugin-qweather-tool

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.x-green.svg)](https://github.com/Soulter/AstrBot)
[![Tests](https://img.shields.io/badge/Tests-48%20Passed-success.svg)]()
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

面向 [AstrBot](https://github.com/Soulter/AstrBot) LLM Agent 的**上下文感知和风天气插件**。

遵循 **[SPEC v1.3](docs/AstrBot-Weather-Tool-SPEC-v1.3.md)** 规范构建，采用严谨的测试驱动开发（TDD），为大模型对话提供客观、准确、抗幻觉的天气事实与穿衣温差决策依据。

---

## 🌟 核心设计理念

- **单一公开 Tool（The Single Seam）**：仅向 AstrBot 注册唯一公开 LLM 工具 `@filter.llm_tool(name="get_weather")`，入参严格收敛为 `(location, when, focus)`。无多余路由 Agent、关键词消息拦截器或第二个决策 AI。
- **原生 LLM 自然作答**：插件仅负责采集并组装“客观天气事实”，自然回复由当前 AstrBot 宿主 LLM 统一生成，完全保留宿主原有的人设、语气与个性风格。
- **零消息副反应**：工具直接返回 JSON 字符串，将结构化事实交还给 Agent 生成最终回答；不使用会触发框架直接发送消息的 `event.plain_result()`，也不调用 `event.send()`。
- **零供应商侵入**：微信端最终回复绝不自动追加任何天气服务商名称、官网链接或宣传尾注。
- **微信纯文本回复**：本轮调用天气工具后，微信回复会清理粗体星号、标题、行内代码等常见 Markdown 标记，保留天气数值和换行；同时覆盖普通回复与工具直接发送的文本。
- **严禁虚构天气**：缺少数据或历史未授权时坦诚降级并返回机器可读 warning，绝不拼凑伪造温度或将缺失降雨概率当成 0。

---

## ✨ 核心特性

### 1. 5 级精确地名与行政区消歧（LocationResolver）
- 依据 5 层优先级链条自动优选：
  1. **显式行政区硬约束**（如“江苏苏州昆山”绝不匹配其他省份的同名地点）；
  2. **有效上下文地点复用**（继承当前会话已确认的 LocationID）；
  3. **精确地名全匹配**（例如搜索“昆山”时，优先选取精确同名的“昆山”，而不是热度数值更高的“昆山区”）；
  4. **行政类型优先级**（城市级优先）；
  5. **热度（Rank）分差阈值自选**（分差 $\ge 15$ 时自动优选，分差过小或缺少 Rank 时返回最多 3 个行政区候选让模型自然澄清）。
- `default_city` 仅在**私聊**生效，群聊绝不借用管理员默认城市。

### 2. 多轮对话与 Pending 澄清机制（WeatherContext）
- 会话状态独立隔离于 `(platform_id, unified_msg_origin, session_id)`。
- 已确认地点保留 7 天，有效天气话题保留 60 分钟。
- **地点追问（Pending）**：当用户提问未提供城市时（如“明天穿什么”），插件安全保存 Pending 意图（10 分钟 TTL）；当用户次轮仅回复城市名（如“昆山”）时，自动恢复原穿衣意图。
- **群聊发起人隔离**：群聊中的 Pending 请求严格按发起人 `requester_id` 隔离，其他群成员发消息绝不会误抢占他人的待澄清状态。
- **跨午夜时间锚点稳定性**：记录初次提问时的 UTC 锚点 `anchor_utc`。23:59 发起提问、00:01 补充城市时，仍然根据原提问时间锚定“明天”，防止因跨午夜导致日期向后漂移一天。

### 3. 时间语义解析与查询规划（TimeResolver & QueryPlanner）
- 注入式 UTC 时钟与目标城市 IANA 本地时区自动转换（`ZoneInfo`），完美支持夏令时与跨日历日推算。
- 完整覆盖标准时间文法与自然中文：`now`, `today`, `yesterday`, `tomorrow`, `+Nd`, `-Nd`, `YYYY-MM-DD`, `@morning`, `@afternoon`, `@evening`, `@night`, `@HH:MM`，以及跨午夜时间区间 `today@evening to tomorrow@morning`。
- **数据源隔离路由**：
  - 询问“现在”优先调用实时天气（Current）；
  - 询问“今天整体”优先调用每日预报（Daily）；
  - 询问“明天/未来具体时段”调用逐小时预报（Hourly），**绝对不把 Current 当作未来预报**；
  - 超过 10 天的预报返回 `unsupported_period`，不凭经验胡编。

### 4. 穿衣与温差客观证据引擎（TemperatureComparator）
- **严格的可比性校验**：不同地点、不同时间窗口长度或不同指标之间拒绝比较，置 `comparison.available=false` 并标明原因。
- **确定性温差计算**：`delta_temp_c = round(target - baseline, 1)`。
- **非硬性变化提示**：提供客观启发式标签（`<3°C` 变化较小、`3–<5°C` 值得留意、`5–<8°C` 明显变化、`≥8°C` 较大变化），结合绝对温度与风雨情况给模型提供输入，而非机械套用穿衣模板。
- **结构化证据包（clothing_evidence）**：返回目标时段绝对气温、体感温度、风速、降雨预期和温差数据。
- **历史数据接入与优雅降级**：对接 QWeather Historical v7（`/v7/historical/weather`）获取昨日客观再分析数据；当 API Key 无历史权限（401/403）时，优雅降级为 `partial` 状态并携带 `historical_not_entitled` 警告，当前天气正常服务，绝不崩溃。
- **过去时段保护**：下午 15:00 询问今早 07:00 时，不拿 Current 天气假冒今早实况，标记 `past_hour_unsupported`。

### 5. 生产级加固与性能防护
- **单实例有界 TTL 缓存**：
  - 地点解析：7 天
  - 实时天气：10 分钟
  - 逐小时预报：15 分钟
  - 每日预报：30 分钟
  - 历史天气：24 小时
- **异步 Single-Flight 并发去重**：群聊中多人同时查询相同城市与预报时，将并发请求合并为单次上游请求，防止请求风暴与额度耗尽。
- **凭据与隐私保护**：API Key、微信原始消息内容与用户个人标识绝不输出到日志或 Tool Result 响应中。
- **稳定错误码映射**：鉴权异常映射为 `auth_error`、限流映射为 `rate_limited`、网络或服务故障映射为 `provider_unavailable`、非法结构映射为 `invalid_request`。

---

## 📦 安装与配置

### 1. 环境依赖
- Python >= 3.10
- AstrBot >= 4.x
- `httpx>=0.28.0`

### 2. 安装插件
进入 AstrBot 的插件目录克隆本项目：

```bash
cd data/plugins
git clone https://github.com/neozhu/astrbot-plugin-qweather-tool.git astrbot_plugin_qweather_tool
pip install -r astrbot_plugin_qweather_tool/requirements.txt
```

### 3. 配置参数
在 AstrBot Web 控制台的插件配置面板中，配置以下 3 项参数（仅暴露 3 项核心配置，其余内部常量经优化无需调整）：

| 配置项 | 类型 | 敏感 | 默认值 | 说明 |
|---|---|:---:|---|---|
| `api_host` | string | 否 | `""` | 和风天气专属 API Host，例如 `abcxyz.qweatherapi.com`（请勿带 `https://` 前缀） |
| `api_key` | string | 是 | `""` | 和风天气 API Key（后台配置输入时自动遮罩保护） |
| `default_city` | string | 否 | `""` | 私聊默认查询城市（建议填写完整地名，如 `江苏省苏州市昆山市`；群聊不生效） |

> **安全提示**：请妥善保护 AstrBot 配置文件及备份，避免将包含 `api_key` 的配置提交至公开代码仓库。

### 4. 简单天气测试

保存插件配置后，在 AstrBot 控制台的 **插件管理 → 和风天气工具 → 插件详情** 中打开 **「连接测试与调试」** 视图。

点击 **「开始测试」**，使用已保存的域名和密钥查询默认城市的当前天气（默认城市未设置时查询北京），显示成功或失败及原始 JSON 结果。

测试视图通过 AstrBot 官方 bridge 使用当前控制台的登录授权，不要直接打开 HTML 文件或 `/api/plug/...` 地址。

---

## 💬 对话交互示例

### 场景 1：实时天气与默认城市（私聊）
> **用户**：现在冷吗？  
> **LLM** → `get_weather(location="", when="now", focus="clothing")`  
> **Tool** → *(自动套用私聊默认城市昆山，获取当前实况)*  
> **LLM**：现在昆山气温 18°C，体感比较舒适，微风，暂时不需要加厚外套。

### 场景 2：省略地点与自动追问（群聊 / 私聊）
> **用户**：明天穿什么？  
> **LLM** → `get_weather(location="", when="tomorrow", focus="clothing")`  
> **Tool** → `{"status": "need_location", "pending_saved": true}`  
> **LLM**：你想了解哪个城市的天气？我帮你看看明天需要穿什么衣服。  
> **用户**：昆山  
> **LLM** → `get_weather(location="昆山", when="", focus="")`  
> **Tool** → *(从 Pending 恢复明天的穿衣查询，计算目标气温与温差)*  
> **LLM**：明早昆山大约 8°C，比今天早上低了 5 度左右，而且有小雨。建议里面多穿一件，出门带好雨伞。

### 场景 3：多轮对话上下文继承
> **用户**：明天昆山会下雨吗？  
> **LLM** → `get_weather(location="昆山", when="tomorrow", focus="rain")`  
> **LLM**：明天全天阴有小雨，降雨概率较高。  
> **用户**：那下午呢？  
> **LLM** → `get_weather(location="", when="tomorrow@afternoon", focus="")`  
> **Tool** → *(自动继承地点“昆山”与关注点“rain”，查询下午逐小时预报)*  
> **LLM**：明天下午 12 点到 18 点之间降雨最为集中，出门建议备好雨具。  
> **用户**：换成苏州呢？  
> **LLM** → `get_weather(location="苏州", when="", focus="")`  
> **Tool** → *(更新地点为“苏州”，继承明天下午的降雨查询)*  
> **LLM**：苏州明天下午也是小雨天气，气温在 14°C 左右。

### 场景 4：显式温差与跨跨时段趋势
> **用户**：今晚到明早会不会大降温？  
> **LLM** → `get_weather(location="昆山", when="today@evening to tomorrow@morning", focus="comparison")`  
> **Tool** → *(解析跨午夜逐小时时间区间，提取夜间温度走势)*  
> **LLM**：今晚到明早温度会从 15°C 逐渐下降至 6°C，降幅达到 9°C，早晚温差很大，明早出门务必注意保暖。

---

## 🏛️ 项目结构

```text
astrbot-plugin-qweather-tool/
├── main.py                    # AstrBot Star 插件入口、get_weather 注册、生命周期
├── weather_service.py         # 业务编排、QueryPlanner、Tool Result 组装
├── qweather_client.py         # 和风天气异步 HTTP 客户端、HTTPS 校验、QWeatherError 映射
├── location_resolver.py       # GeoAPI 5 级候选消歧与行政区约束
├── time_resolver.py           # 本地时区、时间文法、跨午夜区间解析
├── weather_context.py         # 会话作用域管理、Pending 状态暂存与发起人隔离
├── temperature_comparator.py  # 客观温差比较、同口径校验、穿衣事实证据
├── weather_models.py          # 数据结构定义与 Normalizer（Current/Daily/Hourly）
├── cache.py                   # 有界内存 TTLCache 与 SingleFlight 并发去重
├── _conf_schema.json          # 3 项配置定义
├── metadata.yaml              # 插件元信息
├── requirements.txt           # 运行依赖
├── docs/                      # 规范设计与实施报告
│   ├── AstrBot-Weather-Tool-SPEC-v1.3.md
│   └── IMPLEMENTATION-REPORT.md
└── tests/                     # 自动化测试套件（46 个用例）
    ├── test_tool_contract.py
    ├── test_location.py
    ├── test_current_weather.py
    ├── test_time_and_query_planner.py
    ├── test_context_pending.py
    ├── test_comparator.py
    ├── test_cache_and_hardening.py
    └── test_acceptance_scenarios.py
```

---

## 🧪 自动化测试

项目内置完整的离线测试套件，无需连接外部网络或消耗真实 API 额度：

```bash
# 安装测试依赖
pip install pytest pytest-asyncio

# 运行全量测试
pytest -v
```

测试覆盖 SPEC v1.3 中定义的 75 项合约与验收场景（C01–C75）：
- 地点消歧与 Rank 阈值（`昆山` vs `昆山区`）
- 降雨概率比例与空值保护（Current precip_prob strictly `None`）
- 跨午夜时间区间与锚点稳定（C41, C62）
- 群聊 Pending 隔离与私聊默认城市隔离
- 历史 v7 鉴权失败优雅降级
- 过去时段保护与不支持时段拦截
- 单实例 TTL 缓存命中与 Single-Flight 并发去重

---

## ⚠️ 已知限制与说明（SPEC §21）

1. **LLM 自主决策特性**：插件通过详尽的 Tool Description 引导模型正确决策，但不同模型（如 GPT-4o、Claude 3.5、DeepSeek 等）仍可能存在少许误触发或漏触发，此属大模型工具调用的固有特性。
2. **历史再分析数据权限**：QWeather 历史数据接口（Time Machine）依赖开发者账号的具体订阅计划；若账号无权限，插件会自动以 `partial` 降级，不影响当日和未来的预报服务。
3. **已过时段数据限制**：QWeather 不支持回溯查询当天已经过去的逐小时实况（例如下午 15:00 无法补查今早 07:00 实况）；遇到此类请求插件会坦诚提示数据不足，绝不拿当前天气冒充今早实况。
4. **群聊唤醒依赖 Adapter**：在微信群聊中，插件依赖 AstrBot 适配器正确解析 `@机器人` 消息并送入 Agent 流程；未唤醒的群消息插件不作监听与处理。
5. **内存状态生命周期**：会话上下文与 Pending 状态保存在进程内存中，AstrBot 重启或插件重载后临时上下文会重置。
6. **商用授权与标注**：根据产品设计，微信回复中不展示气象数据来源标注；请在商业部署前确认您的天气数据使用授权范围。

---

## 📄 许可证

本项目采用 [MIT 许可证](LICENSE)。
