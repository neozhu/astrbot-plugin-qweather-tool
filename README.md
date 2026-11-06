# astrbot-plugin-qweather-tool

[![CI](https://github.com/neozhu/astrbot-plugin-qweather-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/neozhu/astrbot-plugin-qweather-tool/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.x-green.svg)](https://github.com/Soulter/AstrBot)
[![Tests](https://img.shields.io/badge/Tests-120%20Passed-success.svg)]()
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

面向 [AstrBot](https://github.com/Soulter/AstrBot) LLM Agent 的**上下文感知和风天气插件**。

遵循 **[SPEC v1.3](docs/AstrBot-Weather-Tool-SPEC-v1.3.md)** 规范构建，采用严谨的测试驱动开发（TDD），为大模型对话提供客观、准确、抗幻觉的天气事实与穿衣温差决策依据。

---

## 天气简报：数据更完整，回答保持简洁

普通天气查询默认补充当前气温与体感、全天高低温和昼夜天气、风与湿度、相关逐小时变化，以及穿衣、紫外线、运动和洗车指数。当天查询还会尝试获取当前空气质量和有效天气预警。最终仍由 AstrBot 当前模型结合用户原话与上下文自然作答，只选择与本次需求相关的事实，默认纯文本，不添加图标或表情。单项问题通常一两句；需要做决定时先回答结论，再给关键依据和必要准备；泛问天气时简要概括整体感受、显著变化和必要准备。用户明确要求简短或详细时优先遵从。

回答不采用固定播报顺序，也不要求每次覆盖气温、穿衣等全部项目。`focus` 仅是取数线索，具体活动和需求由当前模型结合完整对话理解，不依赖穷举问法。追问只补充本轮关心的信息；仅借天气表达情绪时自然回应，不主动播报。偏好和安排只使用用户已明确提供的信息，只有缺失信息会明显改变结论时才追问。湿度、空气质量和生活指数按相关性选用，重要且适用于相关地点和时段的预警优先。生活指数是供应商数据，不是插件自行生成；没有权限或缺失时不会编造。指数覆盖今天及未来两天；实时空气质量和当前天气不会作为未来预报使用。小时数据只代表返回的时刻，单小时最高降雨概率不代表全天概率。

回复引导同时覆盖工具描述、工具结果以及启用工具时的请求提示，复用上下文而未再次调用工具的追问也有相关性与去重复提示。这些提示保留 AstrBot 当前人格，不增加意图分类模型或工具参数。离线测试仅验证事实与提示传递，不能证明最终措辞自然；部署后可在同一人格下分别试问“现在多少度”“今晚在外面待很久，需要准备什么”“雨停后还有多久适合出门”，以及连续追问“明天穿什么”→“早上骑车呢”→“那带伞吗”，检查是否直接回答、证据匹配时段、避免无关建议和重复播报，并使用未参与提示调整的陌生问法复查。

新增接口使用现有 `api_host` 和 `api_key`，无需增加插件配置：`/v7/indices/3d`（类型 1、2、3、5）、`/airquality/v1/current/{lat}/{lon}`、`/v7/warning/now`。可用性取决于账号权限、订阅和地区覆盖，可能产生额外 API 调用费用。独立补充请求并发执行，单项补充有 4.5 秒总等待上限；数据分别缓存（指数 1 小时、空气质量 10 分钟、预警 5 分钟），失败短暂缓存 1 分钟，避免重复请求。基础天气缓存与并发去重保持不变。

补充接口失败不会丢弃已有基本天气；内部 `brief.warnings` 区分缺失能力，预警接口不可用不等于“没有预警”。当前实现已用离线 HTTP 响应验证，真实账号授权和宿主模型最终措辞仍需实际部署验证。

此功能明确扩展原 SPEC v1.3 中“不接生活指数/空气质量”的范围，保留单一工具、三个配置参数和宿主 LLM 回答的架构。

### QWeather API 使用清单

以下接口统一使用插件配置中的 `api_host` 作为 HTTPS 域名，通过 `X-QW-Api-Key` 请求头传入 `api_key`。路径中的 `{lat}` 和 `{lon}` 分别为纬度、经度，保留两位小数；城市查询和历史数据等接口通过参数传入地点 ID。

| 接口路径 | 用途 | 调用条件 |
|---|---|---|
| `/geo/v2/city/lookup` | 搜索城市，获取地点 ID、经纬度、行政区和时区 | 需要解析新地点时；可复用已确认地点 |
| `/weather/v1/current/{lat}/{lon}` | 当前天气、气温、体感温度、风、湿度和观测时间 | 查询现在，或为当天简报补充实况 |
| `/weather/v1/daily/{lat}/{lon}` | 全天最高／最低温、白天与夜间天气和风 | 按日期查询，或补充简报的全天概况；请求参数 `days=7` |
| `/weather/v1/hourly/{lat}/{lon}` | 逐小时气温、体感、风、降水量和降雨概率 | 查询具体时段，或补充简报的变化趋势；请求参数 `hours=24`，只使用目标时段实际返回的数据 |
| `/v7/indices/3d` | 运动、洗车、穿衣、紫外线生活指数 | **本次新增**；目标日期在今天及未来两天内时尝试获取，按日期匹配 |
| `/airquality/v1/current/{lat}/{lon}` | 当前空气质量、AQI 及对应指数标准 | **本次新增**；仅为当天查询补充，不作为未来空气质量预报 |
| `/v7/warning/now` | 当前天气预警，保留仍有效的预警信息 | **本次新增**；仅为当天查询补充；接口不可用不等于没有预警 |
| `/v7/historical/weather` | 历史天气，用于与昨日同期比较 | 仅在需要历史对比时调用；需要账号具有相应权限 |

生活指数请求使用 `type=1,2,3,5`：`1` 为运动指数，`2` 为洗车指数，`3` 为穿衣指数，`5` 为紫外线指数。

并非每次查询都调用全部接口：缓存命中时复用已有结果，已确认地点可省去地点搜索，历史接口只在相应对比查询中使用。未来日期不附加当前实况、当前空气质量或当前预警；历史及对比查询保留独立的数据规划，不额外拼接普通简报。

上述为代码使用的接口清单，并不代表每个账号都已开通。生活指数、空气质量、预警和历史数据的授权及覆盖范围须以实际账号为准；当前自动化验证使用离线模拟响应，尚未完成真实账号联调。

## 🌟 核心设计理念

- **单一公开 Tool（The Single Seam）**：仅向 AstrBot 注册唯一公开 LLM 工具 `@filter.llm_tool(name="get_weather")`，入参严格收敛为 `(location, when, focus)`。无多余路由 Agent、关键词消息拦截器或第二个决策 AI。
- **原生 LLM 自然作答**：插件仅负责采集并组装“客观天气事实”，自然回复由当前 AstrBot 宿主 LLM 统一生成，完全保留宿主原有的人设、语气与个性风格。
- **零消息副反应**：工具直接返回 JSON 字符串，将结构化事实交还给 Agent 生成最终回答；不使用会触发框架直接发送消息的 `event.plain_result()`，也不调用 `event.send()`。
- **零供应商侵入**：微信端最终回复绝不自动追加任何天气服务商名称、官网链接或宣传尾注。
- **微信纯文本回复**：微信回复会清理粗体星号、标题、行内代码等常见 Markdown 标记，保留数值和换行；同时覆盖普通回复、复用上下文的回复与工具直接发送的文本，无需本轮调用天气工具。
- **严禁虚构天气**：缺少数据或历史未授权时坦诚降级并返回机器可读 warning，绝不拼凑伪造温度或将缺失降雨概率当成 0。

### 与内置联网搜索共存

启用 AstrBot 内置联网搜索时，插件会在 `get_weather` 可用的请求中追加一次天气工具优先提示，并在工具描述中明确天气查询优先使用 `get_weather`。该提示覆盖所有平台；新闻、网页资料和用户明确要求的来源核验仍可使用搜索工具。

工具选择仍由当前模型决定，提示不能保证每次都选中天气工具。更新插件后可重新加载并新建会话，询问“昆山现在多少度？”并在日志中确认实际调用 `get_weather`。若仍优先搜索，请检查当前人格是否启用了 `get_weather`，以及人格提示是否要求所有实时信息先搜索。供应商原生联网与 AstrBot 内置搜索是不同机制，需另查对应模型配置。

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

配置面板为每个参数提供简短标题和 `hint` 副说明，包含控制台入口、填写示例、常见误填项和生效范围。AstrBot 的 `subtitles` 用于下拉选项的副标题；本插件的三个参数均为自由文本输入，副说明使用配置面板原生支持的 `hint` 展示。

> **安全提示**：请妥善保护 AstrBot 配置文件及备份，避免将包含 `api_key` 的配置提交至公开代码仓库。

### 4. 和风天气工具测试

保存插件配置后，在 AstrBot 控制台的 **插件管理 → 和风天气工具 → 插件详情** 中打开 **「和风天气工具测试」** 视图。

点击 **「开始测试」**，使用已保存的域名与 Key，通过与天气查询相同的 HTTP 客户端调用一次 `/weather/v1/current/39.90/116.41`。实时天气返回有效数据即验证通过，不要求响应包含旧版接口的 `code` 字段。不调用地点搜索，不执行完整天气简报，也不逐个检查生活指数、空气质量、预警或历史接口的权限。

验证使用固定坐标，不依赖默认城市、不修改配置、不发送聊天消息。无效 Key、接口拒绝访问、限流、超时和无效响应分别显示对应原因；网络错误不会被当作 Key 无效。验证通过只说明基础天气调用正常，不代表账号已开通所有附加接口。

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
├── weather_brief.py           # 补充天气、生活指数、空气质量和预警的并发汇总
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
└── tests/                     # 自动化测试套件（120 个 Python 用例及 5 个 JavaScript 用例）
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
# 安装运行及测试依赖（包含 AstrBot 宿主）
python -m pip install -r requirements-dev.txt

# 运行全量测试
pytest -v

# 连接测试视图的 JavaScript 离线测试（Node.js）
node tests/test_plugin_view.mjs
```

### GitHub Actions CI

[CI 工作流](.github/workflows/ci.yml) 在每次推送、Pull Request 和手动触发时运行，使用 Python 3.12、Node.js 24 和 AstrBot 4.29.4。自动检查依赖一致性，运行完整 Python 测试与 JavaScript 视图测试，并上传保留 7 天的 Python JUnit 测试报告。同一分支有新提交时，会取消旧的 CI 运行。

CI 使用离线模拟数据，无需配置 QWeather API Key 或其他 Secrets。可在仓库的 **Actions → CI** 查看运行结果或手动启动。工作流只运行测试，不发布版本或部署插件。

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
