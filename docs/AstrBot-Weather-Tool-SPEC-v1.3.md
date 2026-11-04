# AstrBot QWeather Conversation Plugin — SPEC.md

> **项目名称**：`astrbot_plugin_qweather_tool`  
> **版本**：v1.3（完整开发基线：LLM 参数半结构化 + 无法确认时间默认现在）  
> **日期**：2026-11-04  
> **适用场景**：AstrBot + `astrbot-plugin-wechat-native` + `agent-wechat-native`；微信私聊与群聊 `@机器人`  
> **天气来源**：和风天气 QWeather（开发者专属 `*.qweatherapi.com` API Host）  
> **交付定位**：生产可用的对话增强插件；非天气机器人、非独立 Agent  
> **本文**：完整规格，替代 v1.2；已包含此前全部确定的功能，不需要合并旧版本文件

---

## 0. 版本范围与设计决策

### 0.1 v1.3 的完整功能范围与相对 v1.2 的修改

1. **穿衣推理升级**：结合目标时段绝对气温、今天/明天或昨天/今天的可比温差、体感、风雨及活动场景；相同温差不能机械对应相同穿衣建议。
2. **时间语义增强**：以目标地点时区解析今天、昨天、明天、周末、今晚、明早、跨日与明确时间；比较必须对齐时间窗口与数据口径。
3. **地点澄清续接**：城市缺失/重名时自然追问，用户只答城市或省份即可继续原问题；Pending Request 按请求人作用域隔离。
4. **群聊可靠性**：支持群聊 @ 自然触发；天气上下文跟随 AstrBot 实际 LLM 会话隔离；不同群、不同私聊不互相污染。
5. **轻量生活决策**：围绕出门、穿衣、跑步、骑车、洗车、晾晒等用户当前意图选择必要气象数据；不额外接生活指数 API。
6. **可观测性**：增加结构化 Tool 调用日志与错误分类；用真实对话测试衡量误触发、漏触发、重复调用及回复质量。
7. **修正接口与数据质量**：区分 QWeather v1 嵌套 JSON 和 v7 Historical 旧结构；保留内部数据类型、时效和质量信息，不在微信回复中附加天气供应商名称或网址。
8. **Current 实时天气按需调用**：明确 `/weather/v1/current/{latitude}/{longitude}` 对“现在”问题的优先级；区分“今天”与“此刻”、预报与实况，避免每轮无条件调用 Current。
9. **v1.3：半结构化 Tool 参数**：`focus` 采用受控枚举，`when` 优先采用小型规范表达式；LLM 负责语义归一化，TimeResolver/QueryPlanner 负责确定性计算和路由，兼容常见中文时间输入，不增加第二个 LLM 或 NLP 依赖。
10. **v1.3：无法确认时间默认现在**：若缺少可用时间、无法解析且无法从当前有效天气话题或 Pending 可靠继承，目标时间退化为查询地点当地“现在”，调用 Current；返回明确的回退标记，**不得把现在的天气冒充用户明确提到但无法解析的未来时段**。
11. **v1.3：接口预检**：开发与上线前以实际账号验证 v1 Current/Daily/Hourly、GeoAPI 与有需要时的 v7 Historical 的授权和响应结构；不为了兼容而自动回退到旧版 v7 城市天气预报。
12. **v1.3：同名地点优先选择**：GeoAPI 出现多个候选时按用户明确的行政区、最近已确认的同名地点、完整名称精确匹配、地区类型及官方 rank 依次筛选；在足够明确时自动采用优先候选，真正难以区分时才追问，不新增用户配置。

### 0.2 已确认的架构约束（MUST）

- 只向 LLM 注册 **一个**工具：`get_weather(location, when, focus)`。
- **LLM 自己决定是否调用**，不使用天气关键词触发、正则路由、独立意图分类器或第二个 LLM。
- 插件只查询、验证、归一化数据和计算确定性的对比值；**所有天气解释、穿衣/生活建议与自然语言回复由 AstrBot 原有 LLM 生成**。
- 不通过插件直接 `event.send()` 发送天气结果；不截停原有消息流水线；不产生额外一条天气播报。
- 微信天气回复只包含与用户问题相关的自然语言内容，不主动添加 QWeather/和风天气品牌名称、来源声明、网址或营销性尾注；不实现回复后处理自动追加来源。
- 微信私聊与群聊 @ 使用同一套 `get_weather`、LocationResolver、TimeResolver 和 WeatherService。
- 用户不提供城市时，**只需自然追问城市/地区，不索要街道、GPS 或完整地址**。
- 用户提供城市但 GeoAPI 返回多个候选时，**优先自动选出可信地点**；不因“有多个结果”就机械追问，也不能仅取 API 的第一条。
- 不另外开发用户 UI、微信菜单、图片、天气卡片、定时播报。
- 仅保留 **3 个**用户配置参数：`api_host`、`api_key`、`default_city`。
- 不使用 Decision Provider、Provider Router 或 Tool 执行前后切换 LLM。全程使用 AstrBot 当前聊天的默认 LLM Provider。
- `focus` 是本插件定义的主要意图标签，**不是用户最终输出的语言/风格限制**；`when` 是目标时间而不是让 LLM 自行计算绝对日期的指令。
- **时间兜底原则**：时间未知或无法确定时默认 `now`，只查当前实况；时间信息回退必须可被测试和诊断。

### 0.3 验收标准中的“自然”

“自然”不等于永远查天气。机器人应像普通聊天伙伴一样：需要最新天气事实时调用，普通寒暄时不调用；拿到数据后回答用户正在解决的问题，而不是逐项播报温度、湿度、风速。

---

## 1. 项目目标与非目标

### 1.1 目标用户体验

| 用户输入 | 期望行为 |
|---|---|
| `昆山今天天气如何？` | 以今天的 Daily 概况回答，当前实况有价值时补充 Current |
| `昆山现在多少度？` | 优先 Current，只报告当前附近的天气，不用 Daily 最高/最低冒充实时温度 |
| `今天晚上冷吗？` | 使用今晚的 Hourly，不拿此刻的 Current 代替晚上的气温 |
| `明天会下雨吗？` | 用有效上下文/默认城市；地点未知则追问 |
| `明天穿什么？` | 查询目标天气；可比时自动与今天比较，给出可执行的增减衣建议 |
| `今天比昨天冷吗？` | 查历史（账号可用时），按同时间段/同统计口径比较；历史不可用则说明限制 |
| `明早送孩子上学要加衣服吗？` | 优先明早逐小时体感、风雨；能对齐时与今早比较 |
| `那下午呢？` | 如果当前是天气话题，则继承地点和日期，只修改时段 |
| `换成苏州呢？` | 只修改地点，保留当前天气问题的时间与关注点 |
| `天气怎么样？`（已有有效地点、未说明时间） | 默认 `now`，以 Current 实时天气回答 |
| `明天穿什么？`（地点未知）→ `昆山` | Pending 保留明天和穿衣意图，补充城市后继续原查询 |
| `今天好冷啊` | 可自然闲聊，不强制请求 API |
| `为什么一降雨就闷热？` | 回答气象知识，不请求实时数据 |
| `@机器人 明天上海骑电动车冷吗？` | 群聊内正常 Tool Calling，现有 LLM 生成一次回复 |

### 1.2 非目标（OUT OF SCOPE）

- Slash Command / `/weather`、文本命令触发、固定问答模板。
- 自动识别微信好友真实住址、服务器 IP 定位、微信 GPS/位置附件读取。
- 天气图片/图表/语音/前端天气面板、朋友圈或主动推送。
- 独立 LLM、额外 Prompt 分类 API、多个天气 Tools、MCP Server、n8n 工作流。
- 分钟降水、空气质量、紫外线专项建议、天气预警主动通知、付费生活指数接口。
- 多城市旅行路线规划或大规模跨城市天气对比。
- Redis/SQL/消息队列、跨容器集中状态；v1.3 按 **单 AstrBot 实例**设计。
- 依据天气自动执行任何外部动作，例如发送提醒、改动日程。

---

## 2. 系统架构与职责边界

```text
微信私聊 / 群聊 @机器人
          │
          ▼
agent-wechat-native + astrbot-plugin-wechat-native
          │ 仅负责把消息正确转入 AstrBot（沿用现有唤醒规则）
          ▼
AstrBot 原有 Conversation / Persona / LLM Agent
          │
          ├─ LLM 判断不需要实时天气 ────────────→ 正常生成回复
          │
          └─ LLM 判断需要真实天气
                       │
                       ▼
           get_weather(location, when, focus)   ← 唯一公开 Tool，LLM 优先传规范值
                       │
                       ▼
                WeatherService
                  ├─ SessionScopeResolver / WeatherContext
                  ├─ LocationResolver   → QWeather GeoAPI
                  ├─ TimeResolver       → 规范时间/常见中文/默认 now
                  ├─ QueryPlanner       → focus 枚举 + 时间计划 → Current / Daily / Hourly / Historical
                  ├─ WeatherNormalizer  → 内部统一数据格式
                  ├─ TemperatureComparator → 确定性的同口径温差
                  ├─ ResponseBuilder    → 精简结构化 Tool Result
                  └─ QWeatherClient     → API 鉴权/超时/错误/缓存
                       │
                       ▼
                结构化 Tool Result
                       │
                       ▼
         AstrBot 当前 LLM 继续推理，自然组织最终回复
                       │
                       ▼
                原有微信消息出口（一次）
```

**不得**在 Tool 内再次调用 `context.get_using_provider().text_chat()` 或其他 LLM API。标准 Tool Calling 循环中，工具返回后由 AstrBot 的同一个 Agent 继续推理是正常行为，并不构成插件额外调用模型。

如果 AstrBot 当前使用的 Agent 执行器或 LLM Provider 不支持工具调用，插件应显示为**不兼容运行配置**，不能偷偷回退到消息关键词拦截器。

---

## 3. 智能触发规则（LLM Tool Selection）

### 3.1 唯一判定原则

**只有回答依赖某个地点、某个时间的实际天气事实，且当前对话没有足够新、足够细的可信数据时，才需要 `get_weather`。**

| 类型 | 示例 | Tool 预期 |
|---|---|---|
| 明确查询 | `明天昆山会下雨吗？`、`现在几度？` | Call |
| 天气变化 | `明天比今天冷多少？`、`这两天怎么突然降温？` | Call；需要可比数据 |
| 穿衣问题 | `明天穿薄外套够吗？`、`今天和昨天比要多穿吗？` | Call；考虑温差、绝对温度及体感 |
| 隐含天气依赖 | `下午洗车合适吗？`、`明早跑步要戴手套吗？` | 需要天气事实时 Call |
| 天气话题追问 | `那晚上呢？`、`上海呢？` | 继承必要上下文；数据已足够则不必再 Call |
| 地点澄清 | 上轮机器人问城市；用户答 `江苏昆山` | Call；恢复 Pending |
| 情绪表达 | `今天好冷啊`、`下雨真烦` | 默认不 Call |
| 一般知识 | `为什么冬天冷？`、`什么是寒潮？` | 不 Call |
| 无关话题 | `Blazor 的 SignalR 怎么配置？` | 不 Call |
| 无天气上下文的含糊话 | `明天出门带什么？` | 先正常理解/澄清，不强行认为是天气 |

Tool Description 是**指导而不是强制保证**。对强语义模型做对话回归测试；不新增第二个 Intent LLM。

### 3.2 已有信息与重复 Tool Calling

- 若上一轮 Tool Result 已给出当前问题所需的地点、时间窗口、降雨/温度等事实，LLM 可直接回答。
- 若模型仍再次调用，缓存层应尽量命中并避免额外上游请求。
- 新地点、新日期、原数据过期、需要更高时间分辨率时可再次调用。
- 不用“只要有天气话题就每条消息查一次”的机制。

### 3.3 参数归一化与缺失时间的触发边界

- **LLM 决定工具是否需要调用**；仅当它要调用时才归一化 `when` 和 `focus`，无需为普通聊天额外运行一轮意图模型。
- `get_weather(location="昆山", when="", focus="general")` 表示查询**昆山现在**的天气（前提：无有效待继承时间）；与 `when="today"`（今天整体）不同。
- “那下午呢”应先继承最近有效天气话题的地点及日期，仅替换时段；**不是**因为省略日期就无条件变成现在。
- 当 LLM 提供了无法安全识别的时间文本时，Python 仍按 `now` 查询 Current，但返回 `time_fallback`，最终回复只能陈述当前天气，不得冒充原本想问的未来天气。
- 当用户仅在闲聊而不需要天气事实时，本规则**不能**成为每轮自动查询 Current 的理由。

### 3.4 与原有 Persona 的关系

- Tool 仅提供事实、单位、时间来源和对比数据。
- 大模型沿用现有个性、人设、语言和聊天风格。
- 不提供固定天气播报模板，不规定每句必须包含具体温度。
- 建议微信回复**先给结论，通常 1–3 句**；用户要求更详细时展开。

---

## 4. 唯一公开 Tool：`get_weather`

### 4.1 参数契约（v1.3 MUST）

| 参数 | 类型 | 取值契约 | 默认与安全边界 |
|---|---|---|---|
| `location` | `string` | 用户提到的城市/行政区，如 `昆山`、`江苏省苏州市昆山市`；不要自行编造 GPS | 空字符串由 LocationResolver 按有效上下文/私聊默认城市/追问处理 |
| `when` | `string` | **优先规范表达式**：`now`、`today`、`yesterday`、`tomorrow`、`+Nd`、`-Nd`、`YYYY-MM-DD`；可加时段与比较操作符 | 空值、空白、不支持且无可用继承时**默认 `now`**；不能捏造明确的未来时段 |
| `focus` | `string` | **枚举值**：`general`、`rain`、`clothing`、`comparison`、`outdoor`、`car_wash` | 兼容极少量明确中文别名（如 `穿衣`→`clothing`、`下雨`→`rain`），仍不可识别时降级 `general`，记录 `focus_fallback`，不影响用户原话的自然回复 |

仅有一个公开 Tool，不增加 `compare_to`、`timezone`、`lat`、`lon` 等 LLM 参数。将时段与比较编码于 `when` 中，由 TimeResolver 规范化为内部 TargetPeriod 与可选 BaselinePeriod。

**focus 与 when 不混淆**：`current` 是查询时刻/数据类型，不是 `focus` 枚举成员。`focus="clothing"` 可同时需要实时、预报或温差；是否查询 Current 由时间和问题共同决定，不能只按 `focus` 固定路由。

### 4.2 `when` 规范文法（LLM 优先输出）

```text
DATE       := now | today | yesterday | tomorrow | +Nd | -Nd | YYYY-MM-DD
DAYPART    := morning | forenoon | afternoon | evening | night
TIME       := HH:MM       # 24-hour 00:00..23:59
PERIOD     := DATE [@DAYPART | @TIME]
COMPARISON := PERIOD " vs " PERIOD
RANGE      := PERIOD " to " PERIOD   # 显式跨时段范围
```

- `+2d` = 后天；`+3d@evening` = 大后天傍晚；`tomorrow@07:00` = 明早 7 点。
- `today@morning vs yesterday@morning` = 同窗口比较。**左侧为目标、右侧为基准**；`delta = target - baseline`。
- `today` 是“今天整体”，**不等于** `now`；不加时段时由 QueryPlanner 根据 `focus` 决定 Daily 还是近期 Hourly。
- `now` 是瞬时查询；`now@morning` 这类矛盾组合不可当作有效时段，应记录回退并解析成 `now`。
- `DATE` 的 `YYYY-MM-DD` 只表示用户明确给出的日历日；`LLM` 不应自行猜测“下周末”对应的绝对日期，日期推算交给插件。
- 时段常量仍见第 7 节；“周末”“下周一”等常见中文由固定规则和地点日历处理；LLM 可保留时间短语供兼容解析器识别；未覆盖而不确定时回退 `now`。
- 比较式中任一侧无法解析时，**不得编造比较数据**；可回退成当前天气并将比较标记为不可用。
- 解析 `vs` 和 `to` 必须使用严格分隔符，避免把用户原话中的任意字母当操作符。输入需长度限制以防滥用。

### 4.3 Tool Description（实现必须保留约束）

```python
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
```

> 示例需补齐 AstrBot 实际版本的 import、配置注入和构造。`event.plain_result()` 只作为 Tool Result 回到 Agent，绝不另外发送微信消息；需要验证目标版本 AstrBot docstring 解析格式及 `@filter.llm_tool` 行为。

### 4.4 参数规范化示例

| 用户原话 / 已有上下文 | 建议 `when` | `focus` | 说明 |
|---|---|---|---|
| `现在昆山几度？` | `now` | `general` | Current |
| `昆山天气怎么样？`（无有效天气时段） | `now` | `general` | 缺失时间默认现在 |
| `今天昆山天气怎么样？` | `today` | `general` | 今天整体，Daily 优先 |
| `明天昆山会下雨吗？` | `tomorrow` | `rain` | 明天降雨 |
| `大后天傍晚适合跑步吗？` | `+3d@evening` | `outdoor` | 未来时段 |
| `明早 7 点送孩子上学穿什么？` | `tomorrow@07:00` | `clothing` | 明早时段 |
| `明天比今天冷吗？` | `tomorrow vs today` | `comparison` | 目标/基准双时段 |
| `明天比今天冷吗，要加衣服吗？` | `tomorrow vs today` | `clothing` | 穿衣意图仍需温差 |
| `今晚到明早会不会降温？` | `today@evening to tomorrow@morning` | `comparison` | 跨时段温度趋势 |
| 天气话题 `明天昆山下雨吗` → `那下午呢？` | `tomorrow@afternoon` | `rain` | 继承日期/关注点 |
| `天气会好吗`，没有可确定日期 | `now` | `general` | 当前事实，不声称未来会好 |

### 4.5 参数错误、降级及调用次数保护

- **参数属于可恢复的聊天输入**：`when` 无法解析不返回 `invalid_request`，而是回退 `now` 并在结果中标记 `time_fallback`；`focus` 对固定中文别名做有限映射，仍不可识别则回退 `general` 并标记 `focus_fallback`。
- 不允许从 `focus` 或完整用户问句中用正则猜测日期以假装解析成功。需要多轮时间线索时仅使用已验证的 WeatherContext/Pending。
- 地点仍无法确定时先返回 `need_location`，不得因为时间回退而猜测 GPS、服务器时区或管理员城市。
- 无法解析的明确未来表达（例：`when="某个不清楚的假日晚上"`）回退 `now` 后，只允许回答**当前天气**；不得以该结果推断那个未来晚上会怎样。Tool Result 必须携带提醒 LLM 的机器可读标记。
- `focus="comparison"` 但比较基准缺失/不可解析时，可返回 `now` 实况作为 `partial`，并置 `comparison.available=false`；不构造一个假的昨日/今日差值。
- `invalid_request` 保留给超长输入、明显损坏的结构、安全限制或不合法地点参数，而不是正常的未知时间表达。
- 地点歧义、API 错误和降级不能导致当前同一轮 Tool 循环不断重复尝试；正常上限由 AstrBot Agent 负责。

## 5. 私聊/群聊的会话作用域

### 5.1 必须遵循 AstrBot 实际 LLM 会话

微信原始的私聊/群聊标识、`event.unified_msg_origin`、`event.message_obj.session_id` 及 AstrBot `unique_session` 行为可能影响对话隔离。**不能仅凭群 ID 或 `unified_msg_origin` 的文字形式假定共享策略。**

开发阶段读取当前 AstrBot 版本的 ConversationManager 与 WeChat Native Adapter，验证以下模式：

| 场景 | WeatherContext 作用域 | 备注 |
|---|---|---|
| A 与机器人私聊 | A 的当前 LLM 会话 | 其他私聊用户无法继承 |
| 群 G1 共享模式 | G1 当前 LLM 会话 | B 可以基于 A 已确认的天气话题继续提问 |
| 群 G1 成员隔离模式 | G1 + A 的当前 LLM 会话 | B 不能继承 A 的私有天气上下文 |
| 群 G2 | G2 作用域 | 不继承 G1 |
| 新建/切换 LLM 会话 | 新 Conversation ID | 原天气话题与 pending 不自动继承 |

**推荐 Key**：`(platform_id, unified_msg_origin, conversation_id)` 或经实测与实际 LLM 会话等价的稳定 Key。`conversation_id` 应优先通过当前 ConversationManager 取得；取不到时只允许使用有明确隔离保证的降级 Key。状态绝不可跨真实会话泄露。

### 5.2 天气上下文（WeatherContext）

只存必要事实，不重复保存聊天历史：

```json
{
  "confirmed_location": {
    "location_id": "<GeoAPI-ID>",
    "name": "昆山",
    "adm1": "江苏省",
    "adm2": "苏州市",
    "country": "中国",
    "lat": "<GeoAPI-lat>",
    "lon": "<GeoAPI-lon>",
    "timezone": "Asia/Shanghai"
  },
  "last_period": {
    "start_local": "<ISO-8601>",
    "end_local": "<ISO-8601>",
    "semantic": "tomorrow_afternoon"
  },
  "last_focus": "rain",
  "last_success_at": "<UTC ISO-8601>",
  "pending": null
}
```

- `last_period` 为**绝对时间**，避免一夜过去把旧“明天”理解为新的明天。
- 最近天气**话题**参考 TTL：60 分钟；已确认地点参考 TTL：7 天；均为代码常量，不新增配置。
- TTL 不是“自动触发”开关。超过有效话题窗口后，LLM 不得凭 WeatherContext 强行将普通消息解释为天气。
- 只有确认成功的有效地点可更新 `confirmed_location`；失败、歧义或提供空地点不能覆盖旧正确地点。
- 切换会话或重置，新的 `conversation_id` 不得继承旧 `last_period` / pending。无法监听重置时，需靠会话 ID + TTL 防串。

### 5.3 Pending Weather Request（地点/歧义追问）

Pending 是**待完成的天气意图**，并不是一份新聊天历史。

```json
{
  "kind": "need_location",
  "requester_id": "<stable-sender-id>",
  "scope_key": "<actual-conversation-scope>",
  "requested_focus": "clothing",
  "requested_when": "tomorrow",
  "requested_period": "<absolute period only if resolvable>",
  "anchor_utc": "<original user request timestamp UTC>",
  "comparison": "today_vs_tomorrow",
  "created_at": "<UTC ISO-8601>",
  "expires_at": "<UTC ISO-8601>"
}
```

约束：

1. 默认 10 分钟 TTL；新的**明确**天气请求可以替换同一请求人的旧 Pending。
2. 私聊按当前会话作用域保存。
3. **群聊 Pending 额外按发起人 ID 隔离**，即使群的普通天气上下文是共享的也如此；B 不能仅凭“上海”接管 A 的待澄清穿衣问题。
4. 群消息中若没有可靠的 sender ID，禁止跨成员自动恢复 Pending；应让 LLM 明确复述/澄清意图。
5. 当地时间按目标城市时区解析。地点尚未知时不能假设 Docker 时区；保存 LLM 归一化 `when`（或无法识别的原始时间短文本）、**原问题 UTC 锚点 `anchor_utc`**、`time_fallback` 状态。地点确定后，用原锚点解析相对日期，**不是**用用户补充城市时的当前日期重新解释；否则跨午夜会错一天。
6. 如果原问题未给时间、也无有效待继承时间，Pending 的 `requested_when` 保存 `now`；地点补充时 Current 查询的是**实际执行时**的最新实况，不伪称为原问题时刻的观测。
7. Pending 成功完成后清除；过期不再用于隐式恢复；插件重载后因内存状态丢失可依赖 AstrBot 对话历史恢复，但不能声称一定保留。
8. 原始消息全文、微信昵称或详细私人地址无需存入 Pending。

### 5.4 典型地点追问

```text
用户：明天穿什么衣服？
LLM → get_weather(location="", when="tomorrow", focus="clothing")
Tool → {"status":"need_location", "pending_saved":true}
LLM → 你想看哪个城市？我帮你对比一下今天和明天，看看需要加衣服吗。

用户：昆山
LLM → get_weather(location="昆山", when="", focus="")  # 服务端优先恢复 Pending
Tool → 用 Pending 恢复原穿衣问题；GeoAPI → 今天/明天可比数据
LLM → 结合温差、体感、风雨给自然建议
```

如果 LLM 在第一次**没有调用 Tool**而直接问了城市，第二轮也应能凭 AstrBot 对话历史恢复原意图；不能要求为了保存 Pending 另加第二个 LLM 或关键词消息处理器。

### 5.5 群聊 @ 的边界

- 只有 `@机器人` 消息**实际上进入** AstrBot LLM 流水线才可能触发天气工具。是否需要 @ 由 WeChat Adapter/ AstrBot 既有配置决定，插件不强制绕过。
- 群成员 A：`@机器人 明天昆山会下雨吗` → 正常调用工具。
- 群成员 B：`@机器人 那下午呢` → 仅在共享 LLM 会话且近期仍为天气话题时可继承。
- 群成员 C：`@机器人 解释一下 Python await` → 不触发天气工具。
- 不处理未唤醒的全量群消息，不监听/记录群内所有聊天内容。

---

## 6. 地点解析 LocationResolver

### 6.1 解析优先级

1. 用户**当前消息明确给出的地点**（包括对上一轮歧义的回答）。
2. 同一有效天气话题/同一 Pending 的已确认地点。
3. 同一 LLM 会话最近确认过的地点（在地点 TTL 内）。
4. `default_city`，**仅用于私聊**；群聊不自动套用机器人管理员默认城市。
5. 仍未知 → `need_location`，让 LLM 自然询问城市。

`default_city` 代表**查询默认城市**，不代表某个微信用户的真实所在地。

### 6.2 QWeather GeoAPI 处理

`GET /geo/v2/city/lookup?location=...`，支持 `adm`、`range`、`number`、`lang`。读取 `id,name,adm1,adm2,country,lat,lon,tz,rank` 等；时间解析需要 `tz`。GeoAPI 支持全球地区，不能把所有查询硬编码成 `range=cn`。

- 保留用户显式的国家、省、市等行政信息；必要时用于 `adm`/`range` 和候选过滤。
- 不设置 `number=1` 来假装解决歧义；建议 `number=10`（必要时最多 20），客户端检查候选后再判断。`range` 仅在国家/地区已明确时使用；不得因为输入汉字就强行设置 `range=cn`（例如“东京”）。
- 完全匹配行政层级、同一会话已经确认的同一个 LocationID，优先于单纯 rank。
- 如候选明显唯一且可靠可直接使用；如果多个可信同名候选且缺乏足够证据，返回 `ambiguous_location`（最多 3 项行政区摘要）交给 LLM 追问。
- **禁止只拿 GeoAPI 第一条模糊结果就当作最终地点。**
- 搜索“昆山”不能随意映射到其他地区的同名字串；有“江苏/苏州”信息时应匹配江苏省苏州市昆山市。
- GeoAPI 位置 ID 是本插件的地址标识，不要求用户提供街道地址，更不索取 GPS.

#### 6.2.1 多候选自动优选（MUST）

目标：用户输入“昆山”且 GeoAPI 返回“昆山”“昆山区”等多个结果时，通常直接得到**江苏省苏州市昆山市**，而不是每次追问；同时避免误选同名外国城市或同一国家内其他地区。

`LocationResolver` 必须遵循以下**按顺序执行**的规则。筛选后排序只对有效候选进行，永远不能覆盖用户本轮明确给出的行政区限定：

1. **显式限定优先（硬约束）**：本轮提供了国家、省、市/上级行政区，先验证并过滤 `country/adm1/adm2`；冲突的候选不得因热门或 `rank` 高而入选。必要时使用 GeoAPI 的 `adm`、`range` 缩小查询范围；若无符合项返回未找到/需要澄清，不偷偷改选其他省市。
2. **已确认地点优先（仅同名）**：若用户只给出短名，且当前**同一有效会话**最近确认地点的标准名称也与输入精确相符，可优先复用对应 LocationID（必须确保本轮没有切换省/国家的明确意图）。私聊若配置了完整行政区的 `default_city`，且其城市名称与输入一致，可以作为同名的辅助消歧线索；群聊不使用管理员 `default_city`。
3. **完整名称精确匹配**：对经过首尾空格清理及最小必要的行政后缀规范化后的输入，优先选择标准 `name` 与该输入**完全匹配**的候选；“昆山”比“昆山区/昆山镇/含昆山的其他名字”优先。禁止把任意字符串包含关系当作精确匹配。
4. **地点类型匹配**：用户问的是“城市天气”时，优先 `type=city` 的城市/行政区地点；但不得因此忽略用户明确说出的某个区、县或已确认的地点。这里 `type=city` 是 GeoAPI 类型，不代表一定是中国行政意义上的地级市。
5. **官方 rank 排序**：仅对仍然同等可信的候选，按数值 `rank` 升序排列；**QWeather Rank 取值 1–100，数值越小表示相对重要性越高**。`rank` 是地区重要性，不是“用户真正意图”的概率；不能凭 `rank` 覆盖显式省市或完全匹配。

**自动选择 / 澄清边界**：

- 经过以上规则后如果仅剩一个可信的“完整地名精确匹配”候选，**直接自动使用**。
- 若仍有多个同名精确匹配且没有可靠行政区/会话限定，只有当第一候选的有效 `rank` **至少比第二候选小 15** 时才自动使用第一候选；`15` 是可由测试调整的保守**内部常量**（不是 QWeather 官方置信度阈值，不是新增插件配置）。否则返回 `ambiguous_location`，最多提供三个候选的 `name + adm2 + adm1 + country`，由 LLM 自然追问。
- 如果 `rank` 缺失、不能转成数值、前两名分数接近或排序结果无法可靠区分，则**不要随机挑选**，应澄清。相同分值只允许用 LocationID 稳定排序候选列表，不能将其作为自动确定地址的证据。
- 仅模糊匹配、无完整名称精确匹配时，不能单纯因为 rank 最小就选定；必须有唯一可信匹配或足够的行政区上下文，否则澄清。
- 自动选中时 Tool Result 返回标准地名、行政区和 LocationID，必要时包含内部 `location_resolution.method`（如 `exact_name` / `confirmed_context` / `rank_priority`），方便 LLM 在自然回复中提及实际查询地点以及诊断；**不添加额外独立消息**。
- 只把**最终成功确认/自动优选的有效地点**写入 WeatherContext；歧义候选不会覆盖已有已确认地点。群聊与私聊继续遵守第 5 节隔离策略。

#### 6.2.2 昆山示例（Mock GeoAPI，非实测响应）

```json
{
  "location": [
    {"name":"昆山区","adm2":"某市","adm1":"某省","country":"中国","type":"city","rank":"18","id":"mock-1"},
    {"name":"昆山","adm2":"苏州市","adm1":"江苏省","country":"中国","type":"city","rank":"32","id":"mock-2"}
  ]
}
```

用户输入 `昆山`：即使模糊候选的 `rank=18` 数值更小，也必须优先选择精确地名 `昆山`（示例 LocationID `mock-2`），然后使用真实 GeoAPI 返回的坐标/时区查询天气。示例中的候选名称与分值仅为构造测试数据，不代表真实城市搜索结果。

### 6.3 Geo 数据缓存与许可

仅对实际被查询的**少量地点**做有界 TTL 缓存（建议 7 天/容量上限），不得批量抓取、保存整库或建立 GeoAPI 索引。缓存严格遵循和风天气的 GeoAPI 数据使用许可；如果许可或方案不允许相应缓存，则关闭缓存。

---

## 7. 时间解析 TimeResolver（v1.3 核心契约）

### 7.1 职责分工与优先级

- **LLM**：理解“后天早上”“明天比今天冷”等自然语义并尽量按 4.2 规范化 `when`；不自行计算绝对日期，也不另起一次模型请求。
- **TimeResolver**：严格解析规范时间表达式；兼容常见中文短语；使用地点时区 + 原始消息锚点求绝对日期；产出 `TargetPeriod`、可选 `BaselinePeriod`，以及解析状态；**不靠通用 NLP 模型/库**。
- **QueryPlanner**：根据解析结果和 `focus` 选 Current/Daily/Hourly/Historical；不重新解读中文、不依赖固定回复模板。
- 优先级：**本轮有效明确时间 > 当前请求人的有效 Pending 时间 > 当前会话仍有效的天气话题时段 > 默认 `now`**。
- 如果本轮给出**非空但无法确认**的时间表达式，则直接使用 `now`，标记 `unrecognized_explicit_time`；不能默默继承旧日期从而违背用户本轮意图。
- `when=""` 仅表示本轮未提供明确时间，不等于命令必须忽略 Pending；存在有效 Pending/天气追问时优先继承。
- 独立新请求以原始用户消息时间（UTC）为 `anchor_utc`；Pending 恢复沿用第一次提问的锚点。
- 相对日历日期在确认地点后依据 `location.tz` 计算；禁止使用 Docker/服务器本地时区代替。
- 窗口统一使用带时区日期和半开区间 `[start_local, end_local)`；DST、跨日、跨月、跨年通过 `zoneinfo` 处理；注入 Clock 便于测试。

### 7.2 规范时间表达与常用中文兼容

| 输入 | 解析结果 | 优先 API |
|---|---|---|
| `now` / `现在` / `此刻` | 当前地点的当前时刻 | Current |
| 空字符串且没有有效上下文 | **默认 `now`** | Current |
| 无法识别的时间文本 | **默认 `now` + time_fallback** | Current |
| `today` / `今天` | 当地今天整日 | Daily（需要当前状况时可另查 Current） |
| `tomorrow` / `明天` | 当地次日整日 | Daily |
| `yesterday` / `昨天` | 当地前一日整日 | Historical（有权限时） |
| `+2d` / `后天` | 当地两天后 | Daily/Hourly |
| `+3d@evening` / `大后天傍晚` | 当地三天后傍晚 | Hourly |
| `tomorrow@morning` / `明早` | 当地次日 06:00–09:00 | Hourly |
| `tomorrow@07:00` / `明早7点` | 当地次日 07:00 左右 | Hourly |
| `2026-11-08@afternoon` | 指定日期下午 | Hourly（如在支持范围内） |
| `tomorrow vs today` | 明天为目标、今天为基准 | 同口径比较 |
| `today@morning vs yesterday@morning` | 今天早晨对比昨天早晨 | 历史/现有可比数据 |
| `today@evening to tomorrow@morning` | 今晚到明早连续窗口 | Hourly |
| `那下午呢`（存在天气话题） | 继承先前绝对日期，替换成下午 | Hourly |
| `周末` / `这周六` / `下周一` | 在规则明确时算出当地日期 | Daily/Hourly |
| `最近三天` / `未来一周` | 可计算的日期范围（有效范围内） | Daily/Hourly |

标准表达式 `DATE`、`PERIOD`、`COMPARISON`、`RANGE` 参见第 4.2 节。常见中文支持必须列为有限的、可测试的兼容规则；不是要求实现任意中文 NLP。具体不支持的词组**均回退 now**，不因解析不全而猜日期。

### 7.3 时段约定（可测试代码常量）

| Token | 当地时间窗口 | 备注 |
|---|---|---|
| `morning` | 06:00–09:00 | 早晨、上学前 |
| `forenoon` | 06:00–12:00 | 上午 |
| `afternoon` | 12:00–18:00 | 下午 |
| `evening` | 18:00–24:00 | 晚上、傍晚（v1.3 统一采用此窗口） |
| `night` | 22:00–次日06:00 | 跨午夜 |
| `HH:MM` | 目标时刻附近的最小有效预报窗口 | 不要把 07:00 表示为一天；对 Hourly 可选 07:00 预报时点或相邻点 |

用户明确小时优先；这些窗口是插件的技术定义，**不是** QWeather 官方 Daytime/Nighttime 的窗口。

### 7.4 `now` 回退规则（MUST）

1. 用户只问 `昆山天气怎么样？`，未指定时间且无有效上下文：`when` 解析为 `now`；WeatherService **优先只查 Current**。
2. 用户明确 `今天整体天气`：`today`，**不能**转为 `now`；必须允许 Daily。
3. 用户在天气对话里问 `那下午呢？`：优先使用有效已确认日期，不能因为缺少日期就变成 `now`。
4. `when` 为空而有未过期 Pending（如前轮 `明天穿什么`、本轮补充 `昆山`）：先恢复 Pending 中的 `tomorrow`；不回退 `now`。
5. `when` 显式为无效或无法理解的表达：回退 `now`、`time_resolution.status="fallback_now"`，`reason="unrecognized_explicit_time"`；不计算该未知时间的未来天气。
6. 如果用户说了“不确定何时出门”，通常 `now` 可用于“如果现在出去”的建议；最终回复不得暗示已预测未知的将来时刻。
7. `when="tomorrow vs <unparsed>"` 或其他部分无效比较：不得伪造差值；返回当前可用天气和 `comparison.available=false`，标记 `comparison_period_unresolved`。
8. 当用户还没有给地点时，先进入 `need_location`；一旦得到城市，正常执行时采用新时刻的 Current 实况，但**相对日期**仍用原问题 `anchor_utc` 解释。
9. 回退 `now` 不等于无限期缓存“实时”：Current 仍遵守有效 TTL、观测时间和取回时间区分。
10. `now` 回退**只解决数据检索目标**，不能使无关聊天被强制触发 `get_weather`。

### 7.5 时间解析结果的数据契约

```json
{
  "target": {
    "kind": "instant",
    "start_local": "<ISO-8601-with-offset>",
    "end_local": "<ISO-8601-with-offset>"
  },
  "baseline": null,
  "resolution": {
    "status": "fallback_now",
    "reason": "unrecognized_explicit_time",
    "input": "<short-sanitized-value-or-empty>",
    "anchor_utc": "<ISO-8601-UTC>",
    "timezone": "Asia/Shanghai"
  }
}
```

`resolution.status` 枚举：`explicit`、`inherited_pending`、`inherited_context`、`fallback_now`。若是 `fallback_now`，reason 枚举为 `time_unspecified`、`unrecognized_explicit_time`、`incomplete_comparison` 等。成功解析时 `reason` 为 `null`。结果必须可观察、可测试，不把内部解析异常栈发给 LLM。

### 7.6 `今天`、`现在`与过去时段

- `today` 表示整个当地日历日；当前实况是 `now`，与当天 Daily Forecast 不同。
- “今天晚上”“明早”等目标尚未到来时优先 Hourly；**不拿 Current 代替未来具体时段**。
- “今早 7 点”当地已过时不得用 Current 伪造过去实况；Historical 仅最近 10 天且**不含今天**，无法复原的今早数据明确不可比。
- 明天/昨天比较必须对齐地点、相同时段和统计方式；当前温度不能与别日最高温直接相减。
- 问题跨午夜、跨夏令时、跨地区时按目标地点 ZoneInfo 做本地时间转换；一旦已解析绝对窗口，天气话题后续追问沿用绝对时间而非重新解释相对日。

## 8. QueryPlanner：天气数据如何选择

### 8.1 查询决策表

**先规范化 `focus` / `when`；QueryPlanner 只读规范化结果。** `focus=general` 并且 `when=now` 时优先只调用 Current；`focus=general` 且 `when=today` 时优先 Daily。`focus=clothing` / `comparison` 只影响必要数据与证据，并不能让 `today` 等于 `now`。

| 问题/时间 | 必要数据 | 可选数据 | 限制 |
|---|---|---|---|
| `天气怎么样`、未说明或无法确认时间，且没有有效时段可继承 | **Current** | 无 | 默认 `now`，如显式时间解析失败则返回 fallback 标记；不得声称回答了该未来时段 |
| `现在多少度/现在冷吗` | **Current** | 无 | 只代表当前附近；不得以 Daily 当实况 |
| `现在出去跑步/骑车穿什么` | **Current** | Hourly（涉及接下来时段才需要） | 即时体感、风、降水是主要证据 |
| `今天整体天气怎么样` | **Daily** | Current（确需描述此刻状况时） | 今天是日期，不等于“此刻”；Daily 是概况 |
| `今天下午/今晚会下雨吗`（该时段尚未到来） | **Hourly** | Daily | 不用当前天气代替未来某时段 |
| `明天下午会下雨吗` | **Hourly** | Daily | 只截目标时段 |
| `明早 7 点冷不冷` | **Hourly** | Daily | 逐小时目标附近，不用整天最低温代替 7 点 |
| `今天穿什么`（现在出门/当前时段） | **Current** | Hourly（今日剩余时段）、Daily（全天温差）、昨天 Historical（显式/有价值的比较） | 按问题收敛上游调用，不强制四种数据齐全 |
| `明天穿什么` | **Daily / Hourly**（按时段粒度选择） | 今天同窗口的可比数据 | **默认不调用 Current**；目标绝对温度优先 |
| `明天比今天冷吗` | 两天 **Daily** 或同窗口 **Hourly** | Current（只在明确比较“当前”且可比时） | 最高对最高、最低对最低、同小时对同小时 |
| `现在比昨天同一时间冷吗` | **Current + 昨天 Historical** | 无 | 标明 Current 与历史再分析来源不同，不能伪称完全同口径实测 |
| `今天比昨天冷吗`（全天） | 今日可用 **Daily/Hourly** + 昨天 **Historical** | Current（仅用户询问当前时） | 标明预报与历史再分析差异，无法严格比较则降级 |
| `今早 7 点多少度`（当时段已过去） | 有效的历史/留存记录（如有） | 无 | **不能用 Current 或未来 Hourly 伪造已过时段实况** |
| `洗车/晾晒` | 目标时间及随后相关时段的 **Hourly/Daily** | Current（立即出门或正在下雨时） | 不假装精确判断空气污染/花粉 |
| 10 天后的小时天气 | 无 | 合法范围内较粗粒度的 Daily | Hourly 超出支持范围时明确降级/不支持 |

**优先级：Current 解决“现在（包括无法确认时间的回退）”；Hourly 解决“未来某时段”；Daily 解决“某天整体”；Historical 解决“过去”。** 依据用户意图组合最少必要的数据，**不是每次查 Current + Daily + Hourly + Historical 四个接口**。

### 8.2 `focus` 对 QueryPlanner 的影响

| `focus` | 优先关注的数据 | 说明 |
|---|---|---|
| `general` | 目标时间天气状态/温度 | `when=now` 优先 Current；`today` 优先 Daily |
| `rain` | 降雨现象、降水量、降雨概率（有值才展示） | `now` 查 Current；未来明确时段查 Hourly |
| `clothing` | 绝对温度、体感、风、雨、可比温差 | 依据 `when` 选择 Current/Hourly/Daily；对比可选/显式时必需 |
| `comparison` | 目标/基准温度及时间对齐元数据 | 缺少合法基准时不得计算虚假 `delta` |
| `outdoor` | 温度、风雨、活动所在时间段 | 不额外调用付费生活指数 |
| `car_wash` | 当前/后续降雨时段 | 如 `when=now` 且决策涉及未来几小时，可按需增加 Hourly |

只允许规范枚举及小型固定中文别名映射（`综合天气`→`general`、`降雨/下雨`→`rain`、`穿衣`→`clothing`、`温差/对比`→`comparison`、`户外`→`outdoor`、`洗车`→`car_wash`）；其余非法 `focus` 降为 `general`，警告记录 `focus_fallback`；不因 `focus` 无效就中止天气查询。

### 8.3 API 能力范围

- Current：`GET /weather/v1/current/{latitude}/{longitude}`，返回指定经纬度**当前附近**的气温、体感、天气现象、风、降水等；它是近实时数据，**不能返回昨天温度、今天已过去的逐小时实况或未来某小时预报**。
- Current 响应的 `retrieved_at_utc` 由客户端记录取回时刻；**不得把取回时刻冒充供应商的实际观测时间**。只有官方响应明确提供的时间元数据才能标记为观测/发布时间。
- Daily：未来最多 **10 天**，按 `days=1..10` 请求；依据 `days[].forecastStartTime/forecastEndTime` 与目标地点时区选定日期。Daytime/Nighttime 使用供应商定义的窗口。
- Hourly：未来最多 **240 小时**，按 `hours=1..240` 请求；依据 `hours[].forecastTime` 转为当地时间后筛选。若目标早于最早返回的时刻，不得臆造。
- Historical：`/v7/historical/weather` 仅最近 10 天、**不含今天**，且使用 GeoAPI LocationID + `yyyyMMdd`；能否调用取决于账号和产品权限。
- 超范围返回 `unsupported_period` 或 `partial`，不靠气候常识冒充天气预报。

### 8.4 限制调用成本与并发

- 常见单点天气问题：优先仅 1 次天气 endpoint（GeoAPI 在无缓存时另算），如“现在几度”只请求 Current。
- 穿衣/温差问题：按具体时间与对比需求选择数据源，常见情况 1–2 次主要查询；Current **不是**穿衣请求的必选接口。Historical 失败不得反复重试或阻塞主要建议。
- 可并行的独立 Current/ Daily/ Hourly 请求可以异步并发；结果归一化后统一返回 Tool。
- 对重合范围的数据优先重用 TTL 缓存，不重复下载整套小时预报。

---

## 9. 穿衣与气温变化：核心 v1.3 能力

### 9.1 插件负责“客观事实”，LLM 负责“人类建议”

`focus=clothing` 时，允许 LLM 发送 `when="tomorrow vs today"`，使内部 Comparator 获得目标与基准。若没有合法基准仍须优先回答目标时段穿衣问题，不得因此拒绝全部天气查询。

插件实现 `TemperatureComparator`，计算两段气温是否**可比**、差值多少、参考数据来自哪种预报/历史数据；**不硬编码固定穿衣模板**。

模型根据以下证据自然作答：

1. 目标时段**绝对气温**（例如 24°C、6°C 的穿衣含义不同）。
2. 相同时段气温变化（降温/升温，尽可能同统计口径）。
3. 目标时段体感温度（API 可提供时）、风速/阵风、降雨、天气现象。
4. 白天与早晚差异；出门/骑车/跑步/儿童等具体活动语境。
5. 已由用户说出的个人偏好（如“我特别怕冷”），但不擅自猜测个人健康状况或年龄。

### 9.2 对比优先级与合法性

1. **同地点**：不同城市的温差不能当作“昨天到今天的当地降温”。
2. **同时间窗口**：明早 7–9 点 vs 今早 7–9 点；明天下午 vs 今天下午。
3. **同指标**：气温对气温、体感对体感；每日最高对每日最高、最低对最低；绝不拿今日最高温减昨日当前温度。
4. **同聚合方式**：同窗口逐小时数据聚合应一致（建议算术均值，返回 `aggregation="mean_hourly"`）；要求单点时选目标小时，不随意平均全天。
5. **数据类型透明**：Current（近实时）、Forecast（预报）、Historical Reanalysis（历史再分析）必须在结果里区分。
6. **覆盖率充足**：只有两个窗口都有足够的有效数据点、时间覆盖合理时才计算精确 `delta_temp_c`；否则 `comparison.available=false` 并解释原因。

### 9.3 对比策略

| 用户意图 | 基准 | 目标 | 优先策略 |
|---|---|---|---|
| `明天比今天冷吗` | 今天 | 明天 | 相同时段 Hourly；不可用时 Daily 最高/最低分别对比 |
| `明天穿什么` | 今天可比窗口 | 明天用户将出门的窗口 | 目标绝对温度优先；有合法温差再补充变化 |
| `今天穿什么`（现在出门） | 昨天可比窗口（可选） | 今天当前/之后 | 当前体感优先用 Current；后续变化补充 Hourly，历史不足不影响建议 |
| `今天比昨天冷吗` | 昨天 | 今天 | 历史再分析 vs 今天有来源的气温；必要时说明两者数据口径不同 |
| `今晚到明早降温大吗` | 今晚 | 明早 | 按跨日连续窗口查询 Hourly，说明这是夜间降温 |
| `明早 vs 今早`（今早已过去） | 今天早上 | 明天早上 | 如果没有今天早上可信历史实况/留存预报，**不得给出精确同小时差值**；可提供今天/明天日最低温参考并声明口径 |

> **重要现实限制**：QWeather Time Machine 不包含今天，未来 Hourly 也不能重建“今天已经过去的早上”真实气温。因此如果用户在下午问“明早比今早冷几度”，首次查询可能无法给出精确温差。应优先准确说明数据不足，不为满足对话而计算伪差值。

### 9.4 温差计算与启发式

确定性计算：

```python
# 两个值必须已经过同地点/同时间窗口/同温度指标/同聚合方法校验
delta_temp_c = round(target_temp_c - baseline_temp_c, 1)
# delta < 0: 降温；delta > 0: 升温
```

只给模型提供**非硬性**的变化提示：

| 绝对温差（°C） | 参考标签 | 说明 |
|---:|---|---|
| `< 3` | 变化较小 | 不自动建议加减衣 |
| `3–<5` | 值得留意 | 结合绝对温度与风雨 |
| `5–<8` | 明显变化 | 有必要提醒增减衣 |
| `>=8` | 较大变化 | 更值得提醒，但不能机械指定衣物 |

例如从 30°C 降到 24°C，和从 12°C 降到 6°C，均为 -6°C，但后者可能需要明显更保暖的衣物；不能只看差值。

### 9.5 穿衣输出证据（不是回复模板）

Tool Result 可包含：

```json
{
  "clothing_evidence": {
    "target_temperature_c": 8.0,
    "target_feels_like_c": 5.0,
    "target_wind_speed_ms": 4.0,
    "rain_expected": true,
    "comparison": {
      "available": true,
      "baseline_temp_c": 13.5,
      "target_temp_c": 8.0,
      "delta_temp_c": -5.5,
      "aggregation": "mean_hourly",
      "baseline_kind": "forecast",
      "target_kind": "forecast"
    }
  }
}
```

> 上面是 **模拟数据结构**，不是昆山或其他城市的真实天气；实际值必须来自 API，缺失字段用 `null` 或省略。

LLM 可据此回答“早上比今天冷不少，建议里面多加一层，外套也换厚一点；下雨记得带伞”，但这句回答必须由 LLM 根据数据和用户上下文自行生成。

### 9.6 不要发明天气事实

- `feelsLike` 不存在时不可由 LLM 按公式自行猜一个数，除非明确另行实施经过验证的计算模型（v1.3 不做）。
- 降雨概率没有值时，不允许把 0 作为“不会下雨”。
- `Daily` 最高/最低仅说明全天范围，不代表明早 7 点的精确温度。
- Historical 的“历史再分析”不应称为气象站原始实测值。
- “一定会下雨”“百分百适合跑步”等绝对结论应避免。

---

## 10. QWeather API 接入与字段映射

### 10.1 Host、鉴权、共同约束

- 使用开发者控制台分配的**专属 API Host**：`https://<assigned-host>.qweatherapi.com`。
- v1.3 使用 API Key 请求头：`X-QW-Api-Key: <key>`；不把 Key 放在 URL/日志。
- `lang=zh`；默认 `localTime=false` 接收 UTC 时间，再用 GeoAPI `tz` 转为当地时间。
- 新版天气 API `/weather/v1/*` 使用**纬度/经度**作为路径参数，顺序固定为 `{latitude}/{longitude}`。
- Historical 使用 GeoAPI **LocationID** 和日期，不使用 `/weather/v1/*` 嵌套解析器。
- Host 必须校验为 HTTPS 且在受信任 QWeather 专属域名下；禁止调用任意用户输入 URL（SSRF 防护）。

### 10.2 接口矩阵

**API 家族可混用，但必须在实际 QWeather 账号上完成 Preflight。** v1 天气接口与 GeoAPI v2、Historical v7 具有不同的入参及 JSON Schema；不因旧账号可能存在授权差异就自动切换到旧版 v7 城市天气预报。

| 数据 | Endpoint | 主要参数 | 主要用途 |
|---|---|---|---|
| 城市搜索 | `GET /geo/v2/city/lookup` | `location`、可选 `adm/range/number/lang` | 地名、行政区、LocationID、经纬度、时区 |
| Current | `GET /weather/v1/current/{lat}/{lon}` | `lang`, `localTime` | **当前/即时问题**：温度、体感、风、天气现象及当前降水；不可替代未来预报 |
| Daily | `GET /weather/v1/daily/{lat}/{lon}` | `days=1..10` | 最高/最低、白天夜间天气、日降雨 |
| Hourly | `GET /weather/v1/hourly/{lat}/{lon}` | `hours=1..240` | 逐小时温度、体感、风、降雨概率 |
| Historical | `GET /v7/historical/weather` | `location=<LocationID>`, `date=yyyyMMdd` | 昨天/前天历史再分析（账号可用时） |

### 10.3 实际账号 Preflight 与坐标格式（v1.3 MUST）

- 开发/部署前以**真实账号**和专属 API Host 验证 GeoAPI、Current、Daily、Hourly；如果要提供昨天温差，再验证 Historical。验证 API 返回体中的业务状态、必要字段与账号权限；HTTP 200 不等于 Schema 一定正确。
- v1 纬度/经度路径参数按官方限制**最多两位小数**，保持路径 `{latitude}/{longitude}`；例如 `f"{float(lat):.2f}"`、`f"{float(lon):.2f}"`。原始高精度坐标仍保留在 `ResolvedLocation` 中，格式化**只在 QWeatherClient 组装路径时**执行。
- `ResolvedLocation` 必须保存 GeoAPI LocationID、原始纬度/经度、IANA `tz`；Historical 使用 LocationID + `yyyyMMdd`，不使用经纬度路径。
- 对 401/403/429/404/非预期 Schema 区分原因并记录不含敏感信息的诊断码；不要从 HTTP 状态直接断言一定是付费计划问题。
- 未经账号实测验证，不可在 README 中声称全部服务端点可用；若某产品未授权，按已有降级策略返回 `partial` / `auth_error`。
- 禁止自动退回到计划淘汰的 v7 城市天气接口；如果未来需要支持新版本，在 Client 内按官方规范适配，而不是写多套未经测试的切换器。

### 10.4 新版 v1 格式映射（示意路径）

| QWeather v1 JSON | 内部字段 | 说明 |
|---|---|---|
| Current `temperature.value` | `temperature_c` | 温度单位按返回 `unit` 验证/转换 |
| Current/Hourly `feelsLike.value` | `feels_like_c` | 如供应商未返回则为空 |
| Current/Hourly `condition.text` | `condition_text` | 天气现象，无固定建议 |
| Current/Hourly `wind.speed.value` | `wind_speed_ms` | 验证 `unit` |
| Current `windGust.value` | `wind_gust_ms` | 可选；验证 `unit` |
| Current `precipitation.amount.value` | `precipitation_mm` | 当前降水量；不等同未来降水概率 |
| Current `humidity` | `relative_humidity` | 0..1 比例；可选 |
| Hourly `forecastTime` | `forecast_time_utc` | 转本地后选择目标小时 |
| Hourly `precipitation.probability` | `precip_probability` | **0..1** 比例；展示时 ×100 得 %；**Current 没有该字段时不得推测或设置为 0** |
| Hourly `precipitation.amount.value` | `precipitation_mm` | 不能与概率混淆 |
| Daily `days[].temperatureMax.value` | `day_high_c` | 每日最高 |
| Daily `days[].temperatureMin.value` | `day_low_c` | 每日最低 |
| Daily `days[].daytime` / `nighttime` | `daypart` | 供应商官方白天/夜晚窗口，不等同本项目“上午/下午” |
| Daily `days[].forecastStartTime` | `period_start_utc` | 标记日期窗口 |
| `metadata.attributions` | 不进入 Tool Result | 如 API 返回则仅在内部解析层保留（如有必要），不作为 LLM 输出字段或用户可见标注 |

### 10.5 Historical v7 专用映射

Historical 响应格式示意：

```json
{
  "code": "200",
  "weatherDaily": {
    "date": "<YYYY-MM-DD>",
    "tempMax": "<string>",
    "tempMin": "<string>"
  },
  "weatherHourly": [
    {"time": "<YYYY-MM-DD HH:mm>", "temp": "<string>", "windSpeed": "<string>"}
  ]
}
```

**必须单独解析** `weatherDaily`、`weatherHourly` 和字符串形式的温度；不得尝试从 `hours[].temperature.value` 中读取 v7 历史记录。历史端点示例中的风速/时间表示也不同，应按官方 v7 单位说明验证，不能简单沿用 v1 m/s。

### 10.6 校验规则

- 拒绝不合法 JSON、错误响应码、缺少核心数据的“成功 HTTP”。
- 对缺失字段保留 `null`；不要把 `null` 偷换成 0。
- 湿度/概率如果 API 定义为 `[0,1]`，只在输出面向用户的百分数时乘 100。
- 统一温度为 °C，风速建议内部 m/s；历史单位必须显式转换或避免输出不可比数值。
- 时间戳使用时区安全解析；历史无时区偏移的本地时间结合地点 `tz` 解释，不用服务器时区。
- 保留必要数据来源种类、时间区间和采集时间；不把整份 240 小时原始 JSON 塞入 LLM 上下文。

---

## 11. 结构化 Tool Result 契约

### 11.1 通用成功返回

```json
{
  "status": "ok",
  "source": {
    "data_kinds": ["forecast_hourly"],
    "retrieved_at_utc": "<ISO-8601 UTC>"
  },
  "location": {
    "id": "<GeoAPI LocationID>",
    "name": "昆山",
    "adm1": "江苏省",
    "adm2": "苏州市",
    "country": "中国",
    "timezone": "Asia/Shanghai"
  },
  "query": {
    "focus": "clothing",
    "normalized_when": "tomorrow@morning",
    "time_resolution": {"status": "explicit", "reason": null},
    "target_start_local": "<ISO-8601 with offset>",
    "target_end_local": "<ISO-8601 with offset>",
    "granularity": "hourly"
  },
  "weather": {
    "condition": null,
    "temperature_c": null,
    "feels_like_c": null,
    "temperature_min_c": null,
    "temperature_max_c": null,
    "wind_speed_ms": null,
    "precipitation_probability": null,
    "precipitation_mm": null,
    "relevant_hours": []
  },
  "current_snapshot": null,
  "comparison": {
    "available": false,
    "reason": "insufficient_comparable_data",
    "baseline_date": null,
    "target_date": null,
    "window": null,
    "metric": "air_temperature_c",
    "aggregation": null,
    "baseline_temp_c": null,
    "target_temp_c": null,
    "delta_temp_c": null,
    "baseline_kind": null,
    "target_kind": null
  },
  "warnings": [],
  "from_cache": false
}
```

**`source` 仅表示内部数据类型与取回时间，不包含供 LLM 转述的供应商品牌、网站或来源标注。**

**该 JSON 是契约示例，不是实测天气。** 具体字段依据 `focus` 裁剪；无需所有响应都包含 `comparison`。

`current_snapshot` 为**可选的独立字段**：当结果同时包含 Current 和 Hourly/Daily 时，用于保留当前天气实况，避免与未来目标时段预报混为一谈；例如 `{"kind":"current","temperature_c":18.5,"feels_like_c":17.0,"condition":"多云","wind_speed_ms":2.1,"retrieved_at_utc":"<ISO-8601 UTC>"}`（示例值非实测）。仅查询 Current 时也可把它作为 `weather` 主数据，但 `source.data_kinds` 必须明确含 `"current"`。`retrieved_at_utc` **仅表示取回时刻**，不等于真实观测时刻。

`status="ok"` 仅在主问题有可信天气证据时使用；部分能力失败用 `partial` 并给出 `warnings`。

**v1.3 必须包含 `query.time_resolution`**：回退时例 `{"status":"fallback_now","reason":"time_unspecified"}` 或 `{"status":"fallback_now","reason":"unrecognized_explicit_time"}`。`normalized_when` 填最终执行的时间表达式（通常 `now`），使 LLM 知道本轮查询是“当前实况”，不可宣称已经查询了识别失败的未来时段。`focus` 采用规范枚举值。Tool Result 不需要包含用户完整原话。

### 11.2 非成功返回

| status | 场景 | Tool 必须做什么 | LLM 应做什么 |
|---|---|---|---|
| `need_location` | 无地点、无有效上下文与默认城市 | 保存可用 Pending | 问城市，保持原意图 |
| `ambiguous_location` | 多个可信候选 | 提供行政区候选（最多 3 个），不查询天气 | 自然询问省/市 |
| `invalid_request` | 超长/损坏/恶意结构等不可安全处理的输入；**非正常未知时间表达** | 给稳定 reason code | 不输出内部异常；说明无法处理 |
| `unsupported_period` | 查询超出预报/历史范围 | 不模拟数据 | 说明无法可靠预测/查询 |
| `provider_unavailable` | 超时、5xx、网络故障 | 结构化错误，不暴露细节 | 说明暂时无法获取天气 |
| `auth_error` | Key/Host 无效或没有权限 | 分类记录，不泄露 Key | 说明天气服务不可用 |
| `rate_limited` | 429/配额限制 | 不暴力重试 | 说明暂时受限 |
| `partial` | 主天气可用，但历史/同口径对比缺失 | 提供可用事实及缺失原因 | 仍给出当前可行建议，不捏造温差 |

建议 `warnings[]` 使用稳定 code（如 `historical_not_entitled`、`comparison_not_aligned`、`hourly_out_of_range`、`time_fallback_now`、`focus_fallback_general`），而不是原始异常栈。

### 11.3 响应体积

- 只提供与本轮问题有关的天气证据。对于明早问题不应返回 10 天全部小时数据。
- 小时列表建议在目标窗口内压缩成关键点或有限小时数，保留计算所用数据和时段覆盖。
- 不传输 API Key、原始 URL 凭证、手机号、微信 ID、完整聊天记录。

---

## 12. 最终对话回复的行为规范

### 12.1 优先回答问题，不机械播报

示例：

```text
用户：明天比今天冷吗？
[Tool 返回相同时段平均温度比今天低 5.4°C，明天有风]
机器人：会冷一些，尤其早上，气温大约低 5 度，还有点风。
        如果今天这身刚好，明早建议多加一层。
```

注意：示例温差是假设；真正回复必须根据工具结果。

### 12.2 对话规则

1. 尽可能先给用户关心的结论，再补关键天气依据。
2. 可以结合用户当前话题生成穿衣/出行建议，但不能凭空添加没有查询到的天气事实。
3. 已有天气数据足够时不重复调用；追问只回应变化点。
4. 若 Tool `query.time_resolution.status="fallback_now"`，回答只以“现在/当前”为准；用户明确提过无法识别的未来时段时，不要把当前实况称为对未来的预报，可自然补充“如果你说的是别的时间，告诉我具体日期或时段”。
5. 语气沿用原有 AstrBot Persona，不加入独立插件人格。
6. 地点缺失时只问必要的“哪个城市/地区”，别要求完整地址。
7. 需要比较时只说**有证据**的温差；数据不足可给当前温度建议，并坦诚说明不能精确比较。
8. 不在微信回复中展示原始 JSON、字段名、接口报错或内部诊断。
9. API 出错时不回复“今天晴 20 度”一类虚构内容。
10. 即使用户问“穿什么”，也不把穿衣建议当作医学判断；不擅自推断儿童、老人等的健康状态。
11. 不主动在最终微信回复中附加供应商品牌、天气数据来源网址、宣传文字或固定尾注；不增加消息装饰器来追加此类内容。

### 12.3 几种特别容易出错的情况

- 用户：“我昨天冻得要死，明天还冷吗？” → 查明天，不把用户的“昨天很冷”当作已验证温度数据。
- 用户：“下午出去好不好？”且没有天气/活动上下文 → 不抢先发起天气查询，优先理解用途。
- 群里连续多人插话 → 只使用与当前提问有明确关联的天气语境；避免仅因为近期有天气消息就默认继承。
- 用户仅说“江苏苏州”用于回答地点澄清 → 恢复 Pending；如地点已足够确定则不再重复询问。

---

## 13. HTTP、缓存与可靠性

### 13.1 HTTP 规范

- 使用单一可复用异步 `httpx.AsyncClient` 或 `aiohttp` Session，连接池管理；插件卸载时干净关闭。
- 推荐 connect timeout **3 秒**，单次上游总超时 **8 秒**；按实际部署网络调整代码常量。
- 网络错误或 5xx 可短退避重试最多 1 次；401/403、不合法参数和 Host 错误不重试。
- 对 429 尊重可能存在的 `Retry-After` 与预算，避免递归重试；必要时直接 `rate_limited`。
- 单次用户请求中的若干上游请求需有总耗时预算；Historical 为可降级查询，不得拖垮天气主回答。
- JSON/Schema 必须验证；不能把空字段解释成“晴/不下雨”。

### 13.2 单实例缓存建议

| 类型 | 默认 TTL | 限制 |
|---|---:|---|
| 地点解析 | 7 天 | 只缓存少量用户主动查询位置，不批量建索引 |
| Current | 10 分钟 | 记录客户端取回时间；缓存有效期内可复用，但不能把采集时刻伪称官方观测时刻 |
| Hourly | 15 分钟 | 缓存含覆盖范围和发布时间，按目标时间切片 |
| Daily | 30 分钟 | 根据数据更新规律调整 |
| Historical | 24 小时 | 历史日期为 Key，遵循供应商许可 |

- 缓存 Key：LocationID / 坐标、API、请求范围（days/hours/date）、语言及归一化参数。
- 同 Key 并发用 **single-flight** 去重，防止群里多人同时问天气而重复调用。
- 缓存 TTL 不等于 WeatherContext TTL，两者必须独立。
- 对“现在/当前”问题，优先在有效 TTL 内复用 Current 缓存；TTL 到期后按需重新请求，**不得把过期缓存当作当前天气**。
- 默认不把过期缓存当实时数据；如果未来启用 stale-while-revalidate，必须在 Tool Result 标记 `stale`、数据年龄。
- 有界缓存容量（例如小型 LRU + TTL），不让内存无限增长。

### 13.3 鉴权与历史能力降级

- 历史数据接口可能受订阅/账号权限影响。若 403 或服务权限不足，记录**可读的 capability 状态**，避免每次同一时段连环失败。
- 历史失败时主天气数据可返回 `partial`，让模型仍能回答“明天穿什么”。
- 当前 QWeather 官方文档提示：**2027-01-01 起 API Key 认证会有每日请求量限制**。v1.3 不引入 JWT 配置复杂度，但 QWeatherClient 认证层必须可替换，README 明示迁移风险。

---

## 14. 可观测性与质量评估（v1.3 MUST）

### 14.1 日志要解决什么问题

生产最常见的问题不是“API 是否能返回”，而是：

- **漏触发**：用户问“明早要加衣服吗”，LLM 未调用天气工具。
- **误触发**：用户抱怨“冷死了”，模型每次都查天气。
- **错上下文**：群成员 A 的“昆山明天”被 B 错继承；或一夜跨日导致时间错一天。
- **错数据口径**：拿日最高和当前温度算温差。
- **重复查询/多次回复**：工具重复请求 QWeather 或插件额外发消息。

### 14.2 结构化日志字段

```json
{
  "event": "qweather.tool.completed",
  "trace_id": "<short-random-id>",
  "scope_hash": "<hashed-session-key>",
  "context_kind": "group_shared",
  "location_id": "<GeoAPI-ID-or-null>",
  "intent": "clothing",
  "when_normalized": "tomorrow@morning",
  "time_resolution_status": "explicit",
  "period_kind": "tomorrow_morning",
  "api_calls": ["daily", "hourly"],
  "api_duration_ms": 430,
  "cache_hit_count": 1,
  "status": "ok",
  "comparison_available": true,
  "error_code": null
}
```

- 默认仅记录工具决策结果、天气数据质量、耗时、缓存命中和错误类别。
- 不记录完整微信消息、微信 ID 原文、API Key、个人地址；必要时使用不可逆 hash 做聚合。
- 诊断 Hook 如 `on_using_llm_tool`、`on_llm_tool_respond` 仅在**经过版本验证**后使用；文档标记这两者适用于 AstrBot > v4.23.1。较旧版本可只在 Tool 内记录入参/结果，不为诊断破坏运行兼容性。
- **Tool 内日志无法观察“LLM 没调用 Tool”的漏触发**；漏触发需用标注好的真实对话测试集或人工验收记录。不要宣称加一个 Tool Hook 就能测漏触发。

### 14.3 质量指标（测试阶段，不作为硬编码阈值）

- 显式天气问题 Tool 触发率。
- 隐含天气需求触发准确率。
- 普通闲聊误调用率。
- 地点/日期正确率。
- 对比数据“同时间/同指标/同来源说明”符合率。
- 微信用户可见重复回复次数：目标为 **0（插件造成）**。
- QWeather 请求次数 / 消息、缓存命中率、P95 上游延迟。

不需要在 v1.3 引入 Prometheus/Grafana 或额外数据库，结构化日志 + 测试报告即可。

---

## 15. 配置（只暴露 3 项）

`_conf_schema.json`：

```json
{
  "api_host": {
    "description": "和风天气专属 API Host，例如 abcxyz.qweatherapi.com",
    "type": "string",
    "default": ""
  },
  "api_key": {
    "description": "和风天气 API Key",
    "type": "string",
    "secret": true,
    "default": ""
  },
  "default_city": {
    "description": "私聊默认查询城市，可留空；推荐填写完整行政区，如 江苏省苏州市昆山市",
    "type": "string",
    "default": ""
  }
}
```

- `secret: true` 只控制 AstrBot 配置 UI 的遮罩显示，不意味着配置文件自动加密；README 应提醒保护配置卷及备份。
- 所有 TTL、时段窗口、失败重试、Pending TTL、缓存上限等采用可测试的内部常量，不把插件配置面板做复杂。
- 配置缺失时 Tool 返回 `auth_error` / `provider_unavailable` 的适当安全状态；在启动日志记录简洁配置错误，不输出实际 Key。

---

## 16. 代码结构（优先可维护而非过度分层）

```text
astrbot_plugin_qweather/
├── main.py                    # Star、唯一 get_weather Tool、生命周期
├── weather_service.py         # 编排、QueryPlanner、Tool Result 构造
├── qweather_client.py         # HTTP、认证、接口错误处理
├── location_resolver.py       # GeoAPI 精确选择/歧义处理
├── time_resolver.py           # 目标地点时区与绝对时间区间
├── weather_context.py        # 真实会话作用域、轻量状态、Pending
├── temperature_comparator.py # 同口径比较、覆盖度检查、温差
├── weather_models.py          # 类型/Schema、v1/v7 归一化
├── cache.py                   # 小型有界 TTL / single-flight；必要时可合并
├── _conf_schema.json
├── metadata.yaml
├── requirements.txt           # 仅必要依赖，如 httpx
├── README.md
└── tests/
    ├── fixtures/              # 去敏的 Geo/Current/Daily/Hourly/Historical 模拟 JSON
    ├── test_tool_contract.py
    ├── test_location.py
    ├── test_time_resolver.py
    ├── test_context_pending.py
    ├── test_query_planner.py
    ├── test_qweather_mapping.py
    ├── test_comparator.py
    ├── test_cache_failures.py
    └── test_conversation_acceptance.py
```

文件可少量合并，但不能把 API HTTP、地点消歧、时间解析、温差逻辑全部写进巨大的 `main.py`。

**依赖建议**：Python 标准库 `datetime/zoneinfo/asyncio/json` + `httpx`（或 `aiohttp`，选一种），无须第三方 NLP/Agent/ORM 包。使用 `pytest`/`pytest-asyncio` 作为开发测试依赖即可，不必须安装到生产插件依赖中。

---

## 17. 测试与验收

### 17.1 单元测试（全部 Mock QWeather）

**地点**：GeoAPI 多候选、昆山行政区、苏州/上海切换、海外城市、地点为空、重复名称、候选无时区、GeoAPI 无结果、缓存合法性；测试精确名称优先于模糊名称及 rank、已确认同名 LocationID 复用、行政区硬约束、rank 数值越小越优先、分差不足时澄清、缺失 rank 时澄清、不同群/私聊不得借用地点偏好。

**时间**：规范 `now/today/tomorrow/+Nd/-Nd/YYYY-MM-DD`、`@morning/afternoon/evening/night/HH:MM`、`vs` 比较、`to` 跨时段；中文今天/昨天/明天/大后天傍晚/周末；跨年跨月、目标地点时区、UTC 与 Asia/Shanghai、Europe/Berlin 夏令时、具体小时优先、凌晨跨日、范围超限、Mock Clock；时间空白/无法识别时默认 `now` 并标记原因，Pending 和会话有效时段优先继承。

**上下文**：天气追问继承、话题已过期、地点仍可继承、切换 LLM 会话、新会话重置、多个群互不影响、群共享/群成员隔离、Pending 10 分钟 TTL、不同群成员无法接管 Pending。

**QWeather**：新版 Current/Daily/Hourly 嵌套字段映射、历史 v7 字符串字段映射、单位与比例、空值不视为 0、错误 HTTP/Schema、历史权限不足、全局预算超时、429、缓存及 single-flight。专门测试 Current 不提供 `precipitation.probability` 的情况，以及取回时间不冒充观测时间。

**QueryPlanner**：`when="today"` 不自动等价 Current；空/无可继承/显式无法识别时间统一回退 `now` 并调用 Current；六个 `focus` 枚举含非法回退；此刻只查询 Current、未来时段不借用 Current、今天概况优先 Daily、即时穿衣用 Current（按需 Hourly）、明天穿衣默认不查 Current、过去时段缺资料不伪造实况。

**比较器**：相同小时/同窗口、不同城市拒绝比较、不同指标拒绝、缺少小时点、今早已过去的无可信数据、Daily max/min 正确匹配、负数是降温、历史再分析标签保留、同为 -6°C 的不同绝对温度结果不被固化成相同衣物。

**安全与消息行为**：Tool Result 不泄露 Key、不记录原始聊天消息、不向微信回复附加供应商名称或链接、插件不独立发送消息；`time_fallback` 状态不会导致模型虚构目标时段的天气。

### 17.2 AstrBot + 真正 LLM + 微信对话验收

这些用例应分别测试私聊，以及适用时的群聊 `@机器人`。测试结果记录 `Tool called?`、`QWeather calls`、`LocationID`、`target local period`、`comparison availability`、`reply count`。

| 编号 | 对话或条件 | 预期 |
|---|---|---|
| C01 | `昆山今天天气如何？` | Call；日期按昆山时区；LLM 组织回复 |
| C02 | `明天会下雨吗？` 且私聊有默认城市 | 使用默认城市 |
| C03 | `明天会下雨吗？` 无地点无默认城市 | 自然询问城市，不猜服务器所在地 |
| C04 | `明天穿什么？` → `昆山` | 按 Pending 恢复，直接回答原穿衣问题 |
| C05 | `昆山明天会下雨吗？` → `下午呢？` | 继承昆山/明天，按需 Hourly |
| C06 | 上轮已得小时降雨 → `那带伞吗？` | 足够新时直接回答，无额外上游请求 |
| C07 | `换成苏州呢？` | 仅更新地点，继承时段/关注点 |
| C08 | `今天好冷啊` | 自然寒暄，不必 Call |
| C09 | `为什么冬天更冷？` | 不 Call |
| C10 | `今天冷死了` → `明天还冷吗？` | 第二轮应 Call |
| C11 | `Blazor Server 的 SignalR 如何配置？` | 不 Call |
| C12 | `昆山` 返回多候选且无省市信息 | 精确地名唯一时自动选择；同名仍无法区分才澄清 |
| C13 | `东京明天下午怎么样？` | 按东京当地日期查询，不强行 `range=cn` |
| C14 | `下个月会不会下雨？` 超出预报范围 | `unsupported_period`，不编造 |
| C15 | QWeather 401/403 | 安全降级、仅一次正常回复、不泄露凭证 |
| C16 | QWeather 5xx/timeout | 有界重试；不捏造天气 |
| C17 | `明天比今天冷吗？` | 同口径比较或说明无法精确比较 |
| C18 | `今天比昨天冷吗？` | 历史可用时合法比较，并标记“历史再分析” |
| C19 | Historical 无权限 | 仍能回答今天/明天可用天气，说明不能可靠计算昨日温差 |
| C20 | `明早 7 点送孩子上学要加衣吗？` | 聚焦早上目标小时、体感/风雨，尽量给可执行建议 |
| C21 | `明早比今早低几度？` 且今早已过去无记录 | 不编造精确差值，给能确认的参考数据 |
| C22 | 模拟 30→24°C 与 12→6°C | 同降 6°C，但穿衣建议因绝对温度而不同 |
| C23 | `今晚到明早` | 正确跨午夜提取逐小时数据 |
| C24 | 当前服务器在欧洲、查询昆山恰逢两地不同日期 | “今天”按昆山本地日历 |
| C25 | 今天 23:59 昆山提问“明天” | 次日日期正确，跨年也正确 |
| C26 | 群 G1 A `@机器人 昆山明天会下雨吗` | 群聊可触发唯一 Tool，原流程回复一次 |
| C27 | 群 G1 B `@机器人 那下午呢`（共享会话） | 可继承 G1 的有效天气话题 |
| C28 | 群 G1 B 同样追问（隔离会话） | 不跨成员偷用 A 的上下文 |
| C29 | 群 G1 A Pending 地点、B `@机器人 上海` | B 不能接管 A 的 Pending |
| C30 | 群 G2 追问，不得沿用 G1 的昆山 | 会话隔离正确 |
| C31 | 群 G1 `@机器人 C# async 怎么用` | 不查天气 |
| C32 | 连续问“明天穿什么”“那要带伞吗” | 合理缓存；无多余 API；不重复回复 |
| C33 | 用户说“我比较怕冷”且问明天穿衣 | 尊重用户已给出的偏好，不改变事实数据 |
| C34 | 逐小时返回降雨概率 `0.31` | LLM 可以解释为 31%，不能说 0.31% |
| C35 | 仅能取得 Daily，用户问精确几点下雨 | 说明小时精度不足，不编造具体时刻 |
| C36 | 成功天气回复 | 由原 LLM 自然回复一次；不自动附加供应商名称、网址或来源尾注 |
| C37 | 没有 Tool 调用的普通闲聊 | 不附加天气相关尾注，不影响 Persona |
| C38 | 切换 AstrBot 新对话后说 `那下午呢` | 不悄悄继承旧会话的地点和日期 |
| C39 | 用户回答歧义城市的行政区 `江苏苏州那个` | 只解决歧义并完成原请求，不让用户重问 |
| C40 | 微信群两人快速同时查询同城市同预报 | 上游 single-flight 去重，回复各自正常 |
| C41 | A 在地点未知时当地 23:59 问“明天穿什么”，00:01 才答“昆山” | 按原问题时间锚点解析“明天”，不被补充地点时刻重新推后一天 |
| C42 | `昆山现在多少度？` | 调用 Current；不因“今天”附带 Daily/Hourly |
| C43 | `现在出门骑车冷不冷？` | Current 为主；若需预测接下来几小时才按需 Hourly |
| C44 | `今天整体天气怎么样？` | Daily 为主；Current 只有补充此刻状况确实有用时才查询 |
| C45 | `今晚 20 点会下雨吗？`（目前上午） | Hourly；不拿 Current 的降水状态回答晚上 |
| C46 | `明天穿什么？` | Daily / Hourly；不例行调用 Current，必要时用今天可比窗口 |
| C47 | `现在比昨天这个时候冷吗？` | Current + 昨天 Historical；合法比较并说明历史再分析与近实时来源差异 |
| C48 | 当前下午 15:00，用户问 `今早 7 点多少度？` | 不用 Current 冒充今早；没有可靠数据则说明无法精确提供 |
| C49 | Current 有 `precipitation.amount` 但无 `probability` | 不捏造未来降水概率，也不将缺失当成 0 |
| C50 | 同一地点连续两次询问 `现在几度？`（Current 缓存有效） | 返回有效缓存且可区分取回时间；不重复上游请求或声称秒级实时 |

| C51 | `昆山天气怎么样？`，无时间且无天气上下文 | `when` 默认 `now`，只查 Current，回复当前天气 |
| C52 | `get_weather(昆山, "", general)` 且有效天气话题是明天下午 | 优先继承有效时段而非现在；不得丢失上下文 |
| C53 | `get_weather(昆山, "", general)` 且无任何有效时段 | `fallback_now/time_unspecified`；Current |
| C54 | `when="大后天傍晚"` 或 `+3d@evening` | 解析为地点当地 3 天后 18:00–24:00，Hourly 按覆盖范围切片 |
| C55 | `when="tomorrow@07:00"`, `focus="clothing"` | 正确定位明早 7 点；Hourly；目标时段穿衣证据 |
| C56 | `when="tomorrow vs today"`, `focus="comparison"` | 目标与基准同口径比较，不把 now 混入 |
| C57 | `when="today@morning vs yesterday@morning"` | 在历史/可比数据可用时同时间段比较，否则 partial |
| C58 | `when="today@evening to tomorrow@morning"` | 解析连续跨午夜窗口，不按 today 整日回答 |
| C59 | `when="无法确认的某个节日后"` | 回退 now、标记 unrecognized_explicit_time；仅回答 Current，不宣称未来天气 |
| C60 | `when="明天下午"` | 中文兼容与 `tomorrow@afternoon` 同结果，不增加 NLP 模型 |
| C61 | `focus="current"`、`focus="穿衣"` | `current` 不合法→`general` 并记录 fallback；固定中文别名 `穿衣`→`clothing`；不崩溃 |
| C62 | `明天穿什么？` → 次日 `昆山`（Pending 10 分钟有效，时间跨午夜） | 使用原请求锚点计算 tomorrow；不因时间缺失回退 now |
| C63 | `天气怎么样？` 在群聊且地点未知 | 返回 need_location；地点确认后查询 now Current，不用管理员默认城市 |
| C64 | `focus=comparison` 且一侧时间文本无法识别 | 不计算伪温差；可返回当前实况与 comparison unavailable |
| C65 | `now@morning` 等矛盾规范表达 | 安全回退 now 并注明异常，不按不存在的未来时段回答 |
| C66 | 模拟 QWeather v1 返回 403 / 错误 Schema | 预检标记服务不可用；不自动切 v7 城市预报，不伪造数据 |
| C67 | 传入不同小数长度的经纬度 | 构造 v1 请求时格式化为最多 2 位小数，保留原始 GeoAPI 坐标 |
| C68 | LLM 传 `when=""` 且请求是普通问候 | 原生 Tool Selection 不应因默认 now 规则自动调用天气工具 |
| C69 | `昆山` 搜索返回 `昆山` 和更高热度的 `昆山区` | 优先完全匹配 `昆山`，即使其 rank 数值更大；正确解析江苏苏州的真实候选（使用 Mock） |
| C70 | `江苏苏州昆山`，GeoAPI 返回其他省同名地点 rank 更小 | 仅选行政区匹配的昆山，rank 不覆盖用户限定 |
| C71 | `昆山`，会话最近已确认的地点为江苏苏州昆山 | 同名有效上下文优先复用该 LocationID；无需多余追问 |
| C72 | 两个完全同名、无行政区信息，rank 分别为 20 与 40 | 按内部阈值优选 rank=20 的候选，Tool Result 标明实际选择的行政区 |
| C73 | 两个完全同名、rank 分别为 20 与 25 | 返回 ambiguous_location，最多列出 3 个行政区选项，不直接查询天气 |
| C74 | 两个同名候选缺失/无法解析 rank，且无其他限定 | 返回 ambiguous_location，不能按 GeoAPI 原始顺序猜测 |
| C75 | 群聊 G2 新成员只说 `昆山`，G1 曾确认江苏昆山 | 不跨群使用 G1 会话地点；只根据 G2 有效条件及 GeoAPI 优选 |

### 17.3 测试模型与数据集

- 单元测试不访问真实 QWeather；Mock 固定时间、样例 JSON 和历史权限错误。
- 至少使用一个实际部署的支持 Tool Calling 的 LLM Provider 和当前微信适配器执行端到端测试。
- 为每个用例保存**脱敏**测试结论，而非原始个人聊天记录。
- 修复误触发/漏触发优先调整 Tool Description 与会话语义，**禁止**作为捷径新增全局关键词触发器。
- 文档不要声称 Tool Description 可以达到 100% 准确率；观察真实误判并据此优化。

---

## 18. 运行、部署与安全要求

- 支持 AstrBot 插件正常安装/重载/卸载；`terminate()` 清理 HTTP Client 和后台资源。
- 仅对 Tool 参数进行有限处理，不从任意 URL 下载内容，防 SSRF。
- 插件不得读取系统精确位置、用户 GPS、未经授权的个人地址。
- 避免在日志、异常响应、Tool Result 中输出 QWeather Key 和完整个人消息。
- 仅将最小 WeatherContext 保存在当前进程内存；重启丢失状态是 v1.3 可接受限制，在 README 明示。
- `metadata.yaml` 描述插件、支持平台及版本；不要宣称已经兼容所有 WeChat Adapter 版本。
- 实际兼容版本应在 README 写明：AstrBot version、Python version、LLM Provider、`astrbot-plugin-wechat-native` 版本及测试的 adapter 配置。
- 天气数据使用与缓存遵循实际账号授权范围和接口条款。部署前由项目负责人单独确认当前授权允许不在微信回复中显示来源；若当前授权要求终端标注，需取得适用许可或调整天气数据服务，不得直接宣称省略标注就符合条款。
- 生产启动时检查 API Host/Key 的基本合法性；不能连接外部服务时提供安全错误结果，而不是阻断 AstrBot 普通对话。

---

## 19. 实施步骤（Codex 执行顺序）

### Phase 0 — 先用 Terra 编写契约测试（TDD RED，推荐）

1. 先确定单 Tool 参数、状态枚举和 TimeResolver 规范化契约；Terra 编写不依赖真实网络的参数化单测与去敏 HTTP fixtures。
2. 不用测试锁死内部类名或私有方法；仅对公开契约与可观察行为断言。未实测 API 的字段先根据官方示例制作 fixtures，并明确待实测事项。
3. 确认测试语法/环境正常，失败原因是生产功能未实现；记录 `docs/CONTRACT_QUESTIONS.md`。

### Phase 1 — 原生 Tool 闭环

1. 读取当前目标 AstrBot `@filter.llm_tool`、配置与会话接口文档；确认模型支持 Tool Calling。
2. 实现插件骨架 + 3 配置项 + 唯一 Tool。
3. 使用假数据验证：群聊 @ 与私聊都能调用 Tool；**由原有 LLM 一次性回复**。

### Phase 2 — 数据接入与语义解析

4. 接入 QWeather 专属 Host / API Key / GeoAPI，完成行政区校验、精确地名优选、Rank 低值优先排序及必要时的歧义返回。
5. 实现 Tool 参数归一化（六类 focus、标准 when + 常见中文兜底、默认 now）、TimeResolver（可注入 Clock）和 QueryPlanner（Current/Daily/Hourly）。
6. 实现新版数据 Normalizer 与精简 Tool Result（必须包含 time_resolution）；完成真实 QWeather API Preflight 并记录账号支持情况。

### Phase 3 — 上下文与穿衣增强

7. 根据真实 AstrBot Conversation ID 实现 WeatherContext / Pending；测试群共享与隔离。
8. 接入可选 Historical v7，权限失败可降级。
9. 实现 TemperatureComparator（同窗口、同指标、同聚合，防虚构）。
10. 添加天气生活决策所需事实字段，不生成固定自然语言建议。

### Phase 4 — 生产质量

11. HTTP 限时、重试、缓存、single-flight、稳定错误码。
12. 结构化日志；验证天气回复没有自动追加品牌/链接，也不会产生第二条天气消息。
13. Mock 单测、68 个端到端对话/契约场景、README；运行格式化和静态检查。
14. 移除非必要依赖与架构，确认不扩张范围。

---

## 20. Definition of Done（交付检查表）

- [ ] 只有一个公开的 `get_weather` LLM Tool，无天气关键词触发与第二个 AI。
- [ ] 私聊、群聊 @ 均能通过原有 AstrBot Agent 触发；工具不独立发送天气消息。
- [ ] Tool 成功后的自然天气回复由原有 LLM 生成，语言与 Persona 一致。
- [ ] 地点未知自然追问，用户只补充城市即可恢复原穿衣/天气问题。
- [ ] 同名/模糊地点遵循第 6.2.1 优先级：显式行政区 > 有效已确认同名地点 > 精确名称 > 类型 > rank（数值越小越优先）；无法区分才澄清。
- [ ] 天气上下文与真实 LLM Conversation 作用域一致；群、用户、Pending 隔离正确。
- [ ] 今天/明天/昨天/明早/周末等按照地点时区解析；跨日、DST、跨年测试通过。
- [ ] Tool `focus` 受控枚举；`when` 规范表达式与常见中文兼容，LLM 不负责计算绝对日历日期。
- [ ] 时间未知/无法确认且无有效继承时，默认 `now` / Current；回退被标记且绝不把当前数据冒充未来天气。
- [ ] Current、Daily、Hourly、可用时的 Historical 通过适配器正确解析，不混用新版/旧版 JSON Schema。
- [ ] 真实账号 API Preflight 完成，记录 endpoint 与权限验证；v1 纬经度至多两位小数，历史仍用 LocationID。
- [ ] Current 按“现在”类问题优先调用；“今天”的概况、未来时段和明天穿衣不会无条件调用 Current。
- [ ] 穿衣建议依据目标绝对温度、可比温差、体感、风雨和用户活动，而不是固定温差阈值模板。
- [ ] 历史/当前数据不够时不编造比较值；仍可返回已有的真实天气事实。
- [ ] QWeather API Key 不泄露；超时/401/429/5xx 安全降级。
- [ ] 缓存、并发去重、结构化日志可用；不保留完整个人聊天记录。
- [ ] 微信天气回复不自动附加供应商名称、来源链接或推广尾注；仅通过原有 LLM 流水线回复一次。
- [ ] 部署前独立确认 QWeather 数据使用授权与无聊天来源标注的要求相容。
- [ ] 68 个端到端对话与契约验收场景有记录；核心单测全部通过。
- [ ] README 给出安装、配置、运行前提、兼容性、示例对话和已知限制。

---

## 21. 必须明确披露的限制与取舍

1. **LLM 自主决策不可绝对保证**：Tool Description 增加准确率，但不同模型会出现误触发/漏触发；用测试持续改进。
2. **历史不一定可用**：Time Machine 账号可能无权限；历史数据是再分析数据，不等于站点原始观测；**不含今天**。
3. **今早实况可能无法追溯**：下午首次询问“明早与今早精确温差”，可能得不到可比的今早逐小时实况；必须降级或声明不足。
4. **群聊能力依赖 Adapter**：只有 @ 唤醒消息正常进入 AstrBot LLM 流程时才可用；不绕过平台过滤。
5. **内存状态会随重载消失**：这是单实例轻量架构的可接受限制；对话历史仍由 AstrBot 自己管理。
6. **认证迁移风险**：API Key 在 2027 年初起有每日请求量限制；接口层预留 JWT 替换能力，但 v1.3 不扩展用户配置。
7. **授权前提须确认**：微信对话不展示供应商来源声明；此产品要求与 QWeather 当前数据使用授权是否相容需在部署前确认，必要时另行取得许可或选择允许此模式的数据来源。

---

## 22. 官方文档与实现前检查

> 以上接口及约束基于 2026-11-04 可查的官方文档；未使用项目持有人的 QWeather 凭据做实测。编码及部署时仍须核对目标 AstrBot 版本、开发者账号权限和实际响应 Schema。

- AstrBot 插件开发 / `@filter.llm_tool`：https://docs.astrbot.app/dev/star/plugin.html
- AstrBot 消息与 LLM 生命周期钩子：https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- AstrBot `_conf_schema.json`：https://docs.astrbot.app/dev/star/guides/plugin-config.html
- QWeather API Host：https://dev.qweather.com/en/docs/configuration/api-host/
- QWeather API Key/JWT：https://dev.qweather.com/en/docs/configuration/authentication/
- GeoAPI 城市搜索：https://dev.qweather.com/en/docs/api/geoapi/city-lookup/
- 当前天气 v1：https://dev.qweather.com/en/docs/api/weather/weather-current/
- 每日预报 v1：https://dev.qweather.com/en/docs/api/weather/weather-daily-forecast/
- 小时预报 v1：https://dev.qweather.com/en/docs/api/weather/weather-hourly-forecast/
- 最近 10 天历史再分析 v7：https://dev.qweather.com/en/docs/api/time-machine/time-machine-weather/
- 地理缓存/使用约束：https://dev.qweather.com/en/docs/best-practices/cache/

**v1.3 最终要求：按本规格实现可维护的真实 AstrBot 插件；由同一个 LLM 完成 Tool Selection 和自然回复；时间无法确认默认 now，但不得捏造用户本来关心的未来信息；任何与目标运行版本不兼容的 API 应按真实版本调整，在 README 记录，不得悄悄改变核心产品原则。**
