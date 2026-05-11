# AI 圆桌会议

多角度 AI 协作讨论平台。用户提交议题，多个 AI agent 从不同角度讨论，产出结构化会议纪要。

## 架构

- **后端**: Python FastAPI + SQLite + LiteLLM
- **前端**: React + Vite + TailwindCSS v4
- **实时通信**: SSE (Server-Sent Events)
- **LLM 结构化输出**: `response_format={"type": "json_object"}` + Pydantic `model_validate()` 两层校验
- **部署**: Docker Compose

## 项目结构

```
backend/
  app/
    main.py              # FastAPI 入口
    config.py            # pydantic-settings 环境变量
    routers/
      session.py         # REST API (CRUD + 讨论控制 + 数据池)
      sse.py             # SSE 实时事件流 (EventSource)
    services/
      orchestrator.py    # 讨论流程编排 (后端独立运行，断线不中断)
      llm.py             # LiteLLM acompletion(stream=True) + complete_typed 结构化输出
      search.py          # MCP 搜索 (智谱 ZhipuMCPSearchProvider，复用 LLM API Key)
    agents/
      base.py            # Agent 基类 (动态系统上下文注入 + DebaterState + think/stream/respond/respond_typed)
      moderator.py       # 主持人 (审议/建议/数据边界界定/引导/纪要/思考)
      perspective.py     # 角度嘉宾 (强制数据引用 + think_before_speaking 思考 + 跨轮次状态积累)
      scorer.py          # 评分 (独立0-100 + think_before_scoring 思考)
      data_clerk.py      # 数据研究员 (时间感知搜索 + 预筛查 + 迭代搜索 + 交叉验证)
      prompts/           # Prompt 模板 (.md 文件, 含CoT/Few-shot/角色设定)
    models/
      session.py         # SQLAlchemy 异步模型 (Session/Position/Message/DataPoolItem)
      schemas.py         # Pydantic 结构化输出 (AgentThinking 统一思考模式 + 各类结果schema)
    storage/
      database.py        # async SQLAlchemy engine + 幂等迁移
      repository.py      # 数据访问层 (含数据池 CRUD)
  tests/
    test_session_api.py  # API 端点测试
    test_repository.py   # 数据层测试
    test_orchestrator.py # 编排器集成测试 (mock LLM)
    test_data_clerk.py   # 数据研究员单元+集成测试
    test_data_clerk_new.py # 预筛查 + 迭代搜索测试 (11 tests)
    test_debater_state.py  # 辩手跨轮次状态测试 (13 tests)
    test_rethink_workflow.py # 思考→数据请求→重新思考流程测试 (4 tests)
    test_sse.py          # SSE 测试
frontend/
  src/
    pages/               # Home, Positions, Discussion, Minutes
    components/
      ChatStream.tsx     # token级流式渲染 + [N] citation badges + 可折叠思考面板 (agent_thinking)
      ScorePanel.tsx     # 实时评分面板
      DataPoolPanel.tsx  # 共享数据池面板 (按轮次分组, 可点击URL, 用户贡献)
    hooks/
      useSSE.ts          # SSE 自动重连 (含 lastEventId replay)
    lib/
      api.ts             # API 客户端 (全部接口类型化)
```

## 讨论流程

1. 用户提交议题 → **先搜索议题相关数据** → 主持人审议澄清(有搜索上下文)
2. 主持人建议角度 → **若推荐数据研究员，先搜索议题相关数据，数据注入角度建议**
3. 用户选择角度 + 是否启用数据研究员 → 可预览研究数据
4. **主持人界定数据边界** (具体事件、时间范围、关键实体、相关性规则)
5. 主持人开场(含数据边界说明) → 循环:
   - 数据研究员为每位辩手搜索(**时间感知关键词 + 数据边界约束**)
   - **预筛查**: 仅凭标题+摘要排除明显不相关结果，节省 web_reader 开销
   - **迭代搜索**: 若交叉验证佐证不足(`validated < 2`)，自动生成补充关键词重新搜索
   - 辩手**先思考**(`think_before_speaking` → `AgentThinking` → **若需要数据则触发重新搜索** → 重新思考) → 再发言(**强制引用数据池，遵守数据边界**)
   - 评委**先思考**(`think_before_scoring` → `agent_thinking` SSE) → 再评分(含数据运用维度)
   - 主持人**先思考**(`think_before_judging` → **若需要数据则触发重新搜索**) → 再判断(继续/结束)
6. 用户可在辩论中向数据池添加自己的数据
7. 主持人生成会议纪要

## 核心机制

### 两阶段思考 (Two-Pass CoT)
- **每个 agent 在行动前先思考**：辩手、主持人、评委在发言/判断/评分前，先做一次结构化思考（`respond_typed(AgentThinking)`），思考结果注入第二轮调用的上下文
- **统一 AgentThinking schema**：取代原先多个 role-specific ThinkResult，改为自由思考 + 策略元数据提取
  - `thinking`: 自由思考过程（分析局势、制定策略、发现关键问题等）
  - `data_requests`: 需要数据研究员搜索的关键词列表（触发重新搜索→重新思考循环）
  - `my_arguments_standing/my_arguments_refuted/opponent_weaknesses`: 辩手跨轮次状态积累
  - `chosen_strategy`: 本轮策略 (ATTACK/DEFEND/REDIRECT/EVIDENCE)
- **思考过程前端可见**：`agent_thinking` SSE 事件 → ChatStream 可折叠思考面板（琥珀色边框，自动展开）
- **Feature flag**: `enable_cot: bool = True`（环境变量 `ENABLE_COT=false` 可禁用，回退到单次调用）
- **容错**: 思考调用失败时 log warning 并继续，不阻塞主流程
- **代价**: 每个决策点增加 1 次 LLM 调用（辩手/评分/判断/纪要各+1）
- 思考调用链：`agent.think_before_xxx()` → `BaseAgent.think()` → `complete_typed(AgentThinking)` → 注入到第二轮 prompt

### 辩手跨轮次状态 (DebaterState)
- `base.py: DebaterState` 维护辩手在多轮讨论中的记忆
- **积累机制**: 每轮思考后更新仍站得住的论点、被反驳的论点、对手弱点、策略历史
- **注入 prompt**: 状态自动注入辩手系统提示，辩手知道"哪些论点被反驳了"和"对手弱点是什么"
- **策略检测**: 连续相同策略 ≥ 3 轮时标记，提示辩手变换策略
- **容量控制**: 各列表上限 20 条，自动截断最旧条目

### 数据请求 → 重新思考循环
- 辩手/主持人思考时可通过 `data_requests` 字段请求额外数据
- **触发条件**: `data_requests` 非空，或思考文本匹配"需要数据/缺少数据"等中文模式
- **流程**: 思考 → 发现数据不足 → data_clerk 搜索新数据 → **重新思考**(带新数据) → 行动
- 最多 4 个补充搜索关键词（主持人最多 2 个）
- 重新思考的 SSE phase 标记为 `rethink`，前端可区分

### 系统上下文注入
- `base.py: build_system_context()` 为每个 agent 注入动态系统信息
- 包含当前 UTC 日期时间(精确到分钟)、星期、当前年份
- 所有 agent 自动获取精确时间，搜索不硬编码年份

### 数据边界界定
- `moderator.py: establish_data_scope()` 在辩论开始前分析议题
- **先搜索议题相关数据，边界基于真实数据而非训练数据**
- 输出结构化 DataScope: specific_event / time_range / key_entities / relevance_rule
- 例: "哈登和米切尔今天谁表现好" → 事件=5月7日NBA比赛, 时间=2026年5月7日, 规则=只接受该场数据
- 数据边界文本注入每个辩手 prompt，辩手被告知"超出边界的数据不要引用"

### 数据预筛查 + 迭代搜索
- `data_clerk.py: screen_results()` LLM 快速筛查(仅用标题+摘要)，排除明显不相关/矛盾的结果，节省 web_reader 开销
- `data_clerk.py: research_with_validation()` 完整流水线: 搜索 → 筛查 → 提取 → 交叉验证 → 迭代补充搜索
  - 当 `validated < 2`(互相佐证不足)时，LLM 生成针对性补充搜索关键词，重新搜索、筛查、提取、合并(去重)、再验证
  - 最多 3 轮迭代，`validated >= 2` 提前退出
  - 合并策略: 按 URL 去重，所有轮次结果合并后重新交叉验证
  - 质量标注: high (validated≥2) / medium (部分) / low (无)
- `data_clerk.py: verify_results()` 旧验证方法(兼容保留): 边界范围/多源佐证/池一致性三重验证
- 验证失败(异常)时 fallback 返回全部结果(不阻塞流程)
- **DRY**: `research_with_validation()` 整合了 orchestrator.py / session.py 三处重复的搜索流水线

### 时间感知搜索
- `data_clerk.py: _time_awareness_hint()` 将议题时间语义转为具体日期
- **搜索策略多样性**: 根据时间远近选择不同策略 — 近期事件用"最新/最新比赛"等灵活查询，远期事件可用具体日期
- **查询策略**: 一个中文关键词 + 一个英文关键词，增加搜索覆盖面
- 搜索结果通过 `logger.info` 记录查询关键词和返回条目，可通过 server.log 追踪

### [N] Citation 引用系统
- 数据池条目以 [1]、[2]... 编号注入 agent prompt
- agent 输出中引用为 [1] 标记 (非"根据《XX报道》"格式)
- 前端 ChatStream 解析 [N] 渲染为蓝色上标圆形 badge，点击显示 tooltip (标题+摘要+URL)

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/sessions | 创建讨论 |
| GET | /api/sessions | 列出讨论 |
| GET | /api/sessions/{id} | 获取详情 |
| GET | /api/sessions/{id}/messages | 获取消息 |
| GET | /api/sessions/{id}/minutes | 获取纪要 |
| POST | /api/sessions/{id}/clarify | 议题澄清 (含搜索上下文) |
| POST | /api/sessions/{id}/refine | 更新议题 |
| POST | /api/sessions/{id}/suggest-positions | 建议角度 (含 preliminary_data) |
| POST | /api/sessions/{id}/start | 开始讨论 |
| POST | /api/sessions/{id}/data-pool | 用户添加数据到共享数据池 |
| GET | /api/sessions/{id}/stream | SSE 事件流 |
| GET | /api/health | 健康检查 |

## SSE 事件类型

| 事件 | 说明 |
|------|------|
| discussion_start | 辩论开始，含主持人开场白 |
| round_start | 新轮次开始 |
| data_fetch_start | 数据研究员开始搜索 |
| data_fetch_complete | 搜索+验证完成，返回已验证结果列表 |
| screening_start | 预筛查开始（含 total 条数） |
| screening_result | 预筛查结果（kept/rejected 条数） |
| iterative_search | 迭代补充搜索（含 round/reason/queries） |
| validation_complete | 验证完成（validated/unique/contradictions/quality/iterations） |
| cross_validation_result | 交叉验证结果（validated/unique/contradictions/note） |
| user_data_added | 用户贡献数据 |
| agent_thinking | Agent 思考过程（辩手/评委/主持人的结构化分析），前端可折叠面板展示 |
| agent_message_start/chunk/complete | 辩手流式发言 |
| score_update | 评分更新 |
| moderator_guidance | 主持人引导 |
| round_complete | 轮次结束 |
| discussion_end | 辩论结束，含纪要 |
| error | 错误 |

## 开发

### 重启规则（强制）

每次更新后端代码后必须**彻底重启**，禁止热更新：
```bash
taskkill //F //IM python.exe
sleep 2
cd backend && find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
python -m uvicorn app.main:app --port 8002 > server.log 2>&1 &
sleep 4 && curl -s http://127.0.0.1:8002/api/health
```

> 注意: Windows 下 `curl` 需用 `127.0.0.1` 而非 `localhost`。

### 跨模块同步（强制）

修改任何功能时，必须检查所有相关模块是否需要同步更新：
- orchestrator.py 改了 → 检查 session.py 是否有相同逻辑
- search.py 改了 → 检查 data_clerk.py 是否有相同逻辑
- schemas.py 改了 → 检查所有使用该 schema 的地方
- SSE 事件格式改了 → 检查所有 emit 该事件的文件

```bash
# 修改后立即用 grep 确认所有相关位置
grep -rn "关键字" app/
```

### 开发命令

```bash
# 后端 (需要 Python 3.11+)
cd backend
pip install -e ".[dev]"
# 不要用 --reload，用上面的彻底重启流程

# 前端 (需要 Node 22)
cd frontend
npm install
npm run dev  # http://localhost:5173, 自动代理 /api → localhost:8002

# 测试
cd backend && python -m pytest tests/ -v

# Docker
docker-compose up --build
```

## 环境变量

复制 `.env.example` 为 `.env`，填入 LLM API key。

| 变量 | 必需 | 说明 |
|------|------|------|
| LLM_API_KEY | 是 | LLM API 密钥 (搜索也用此 key) |
| LLM_MODEL | 否 | 默认 openai/glm-5.1 |
| LLM_BASE_URL | 否 | 默认智谱 OpenAI 兼容端点 (/api/paas/v4) |
| SEARCH_PROVIDER | 否 | zhipu (用 MCP 协议搜索，复用 LLM_API_KEY)，留空禁用搜索 |
| DATA_CLERK_MODEL | 否 | 数据研究员专用模型 (默认跟随 LLM_MODEL) |
| ENABLE_COT | 否 | 默认 true，设 false 禁用两阶段思考（回退单次调用） |

## 已修复的关键问题

- 循环导入: models/__init__.py 和 storage/__init__.py 改用延迟导入
- DB会话生命周期: orchestrator 使用独立的 DB session (async_session())，不依赖请求作用域
- SSE unsubscribe: 修复 list.discard → list.remove
- 前端 ScorePanel: 修正 angle_id/angleName 字段映射
- ChatStream processedRef: 两个 useEffect 共享一个 ref 导致消息不显示，拆分为 poolProcessedRef + msgProcessedRef

## 状态

**V1.5 数据预筛查 + 迭代搜索**。65/65 测试通过，前端 TS 编译通过。

### 核心特性

- **两阶段思考**: 每个 agent 在行动前先做结构化思考（think → agent_thinking SSE → 注入思考 → 行动），前端展示可折叠思考面板
- **统一 AgentThinking schema**: 取代多个 role-specific ThinkResult，自由思考 + 策略元数据提取 + data_requests 触发重新搜索
- **辩手跨轮次状态 (DebaterState)**: 积累论点状态、对手弱点、策略历史，跨轮注入 prompt，防止辩手重复已失败的论点
- **数据请求 → 重新思考**: 思考中发现数据不足 → data_clerk 补充搜索 → 重新思考 → 行动，闭环保证数据充分
- **系统上下文注入**: 每个 agent 自动获取当前精确日期时间，搜索不再硬编码年份
- **数据边界界定**: 主持人在辩论开始前分析议题，确定具体事件/时间/实体/相关性规则，约束所有后续辩论
- **数据预筛查 (screen_results)**: 仅凭标题+摘要排除明显不相关结果，节省 web_reader 开销（每轮省 ~8-15s）
- **迭代搜索 (research_with_validation)**: 交叉验证佐证不足时自动生成补充关键词重新搜索，最多 3 轮，`validated >= 2` 提前退出
- **时间感知搜索**: 搜索策略多样性（中英混合关键词），根据时间远近灵活选择查询方式
- **clarify 阶段搜索**: 主持人审议议题前先搜索相关数据，避免因训练数据过时而追问错误问题
- **[N] Citation**: 数据引用使用 [1][2] 编号，前端渲染为可点击蓝色 badge + tooltip
- **LLM 结构化输出**: 两层校验 - `json_object` 模式 + Pydantic `model_validate()`
- **共享数据池**: 持久化到 DataPoolItem 表, 按轮次分组, 前端独立面板展示, 用户可贡献数据
- **MCP 搜索**: 智谱 ZhipuMCPSearchProvider，复用 LLM API Key，无需单独搜索 key

### 详细设计文档

- [Data Clerk Enhancement Plan](C:\Users\16141\.claude\plans\parallel-crunching-fairy.md) — 数据研究员增强（数据池、用户贡献、前端面板）
- [Data Clerk Pre-filter + Iterative Search](C:\Users\16141\.claude\plans\rippling-beaming-lynx.md) — 数据预筛查 + 迭代搜索实施计划
- [Data Clerk Iterative Search Details](C:\Users\16141\.claude\plans\data-clerk-iterative-search.md) — 迭代搜索详细设计（prompts、方法签名、数据流）

### 验证状态
- 后端: 65/65 测试全部通过 (含 11 预筛查/迭代测试 + 13 辩手状态测试 + 4 重新思考流程测试)
- 前端: TypeScript strict mode 编译通过
- 需删除旧 DB 文件重启后端以应用新表迁移

### 已修复的所有问题
- DB session 生命周期: orchestrator 使用独立 session
- 并发启动防护: `_active_sessions` set + 409 Conflict
- 输入验证: Pydantic Field (topic 1-500字符, max_rounds 1-10, angle_ids 2-6)
- seq 持久化: 从 DB 初始化 MAX(seq)
- 错误消毒: 分类映射 timeout/auth/rate → 用户友好提示
- Prompt 缓存: 模板模块级 `_PROMPT_TEMPLATE`
- httpx 复用: 实例级 `_client` + `close()`
- SSE 队列泄漏: bounded queue (200) + event history (500) + reconnect replay
- SSE 重连: 前端 useSSE 追踪 lastEventId，后端支持 replay
- 前端移动端: Discussion 响应式布局 (md:flex-row)，header flex-wrap
- 前端错误状态: alert() → inline error banners + 恢复按钮
- 前端类型安全: api.ts 全部接口类型化
- 前端错误区分: Minutes 区分 "不存在" vs "网络失败"
- ChatStream processedRef: 两个 useEffect 拆分为独立 ref (poolProcessedRef + msgProcessedRef)
- respond_typed 参数顺序: `respond_typed(self, response_model, context, user_message)` 注意 response_model 在前
- SQLite 迁移: 新增 DataPoolItem 表 + preliminary_data 列，需删 DB 重启
- 搜索提供商: 实现 ZhipuMCPSearchProvider (MCP 协议)，复用 LLM API Key
- clarify 阶段搜索: 主持人审议前先搜索，避免因训练数据过时而错误追问
- 搜索策略: 中英混合关键词 + 时间远近自适应查询策略
- 统一 AgentThinking: 取代多个 role-specific ThinkResult schema，统一思考模式 + 策略元数据
- DebaterState: 跨轮次辩手状态积累，防止重复已失败论点
- DRY 搜索流水线: `research_with_validation()` 整合 3 处重复代码 (orchestrator + session clarify + session suggest)
- 数据请求→重新思考: 思考中发现数据不足可触发补充搜索 + 重新思考闭环

### 待做 (V2)
- SQLite → PostgreSQL (生产部署)
- 数据池面板移动端优化
- 辩手引用数据的准确性验证 (防幻觉)
- 前端搜索过程可视化 (展示搜索关键词、搜索结果)
- 思考面板自动折叠 (DeerFlow 风格：展开1秒后自动收起)
- `<thinking>` tag 流式解析 (辩手思考实时流式输出，非一次性展示)
- `DebateThinkResult` 两阶段强化 (辩手先输出结构化思考，再用思考生成流式发言)

### 端到端联调 (V1 MVP 已验证)

使用智谱 GLM-5.1 完成端到端测试:
- LLM 配置: `openai/glm-5.1` + `https://open.bigmodel.cn/api/paas/v4`（必须用 OpenAI 兼容端点，anthropic 兼容端点对中文有编码问题）
- 完整流程: 创建 → 澄清 → 细化 → 角度建议 → 开始讨论 → 纪要生成，全部通过
- 修复了角度 ID 匹配问题: `add_position()` 需传入 LLM 返回的逻辑 ID，否则 start 时 DB ID 匹配不上
- `.env` 文件需放在 `backend/` 目录下

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
