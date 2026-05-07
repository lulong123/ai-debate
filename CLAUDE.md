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
      base.py            # Agent 基类 (动态系统上下文注入 + stream/respond/respond_typed)
      moderator.py       # 主持人 (审议/建议/数据边界界定/引导/纪要/数据研究员推荐)
      perspective.py     # 角度嘉宾 (强制数据引用，遵守数据边界)
      scorer.py          # 评分 (独立0-100, 含数据运用维度)
      data_clerk.py      # 数据研究员 (时间感知搜索 + 数据验证 + 共享数据池)
      prompts/           # Prompt 模板 (.md 文件, 含CoT/Few-shot/角色设定)
    models/
      session.py         # SQLAlchemy 异步模型 (Session/Position/Message/DataPoolItem)
      schemas.py         # Pydantic 结构化输出 (ClarifyResult/PositionsResult/RoundJudgment/ScoreResult/SearchQueries/DataScope/VerifiedResults/DebateMinutes 等)
    storage/
      database.py        # async SQLAlchemy engine + 幂等迁移
      repository.py      # 数据访问层 (含数据池 CRUD)
  tests/
    test_session_api.py  # API 端点测试
    test_repository.py   # 数据层测试
    test_orchestrator.py # 编排器集成测试 (mock LLM)
    test_data_clerk.py   # 数据研究员单元+集成测试
    test_sse.py          # SSE 测试
frontend/
  src/
    pages/               # Home, Positions, Discussion, Minutes
    components/
      ChatStream.tsx     # token级流式渲染 + [N] citation badges (tooltip)
      ScorePanel.tsx     # 实时评分面板
      DataPoolPanel.tsx  # 共享数据池面板 (按轮次分组, 可点击URL, 用户贡献)
    hooks/
      useSSE.ts          # SSE 自动重连 (含 lastEventId replay)
    lib/
      api.ts             # API 客户端 (全部接口类型化)
```

## 讨论流程

1. 用户提交议题 → 主持人审议澄清
2. 主持人建议角度 → **若推荐数据研究员，先搜索议题相关数据，数据注入角度建议**
3. 用户选择角度 + 是否启用数据研究员 → 可预览研究数据
4. **主持人界定数据边界** (具体事件、时间范围、关键实体、相关性规则)
5. 主持人开场(含数据边界说明) → 循环:
   - 数据研究员为每位辩手搜索(**时间感知关键词 + 数据边界约束**)
   - **数据研究员验证搜索结果**(边界内? 多源佐证? 与池一致?)
   - 嘉宾发言(**强制引用数据池，遵守数据边界**) → 评分(含数据运用维度) → 主持人判断
6. 用户可在辩论中向数据池添加自己的数据
7. 主持人生成会议纪要

## 核心机制

### 系统上下文注入
- `base.py: build_system_context()` 为每个 agent 注入动态系统信息
- 包含当前 UTC 日期时间(精确到分钟)、星期、当前年份
- 所有 agent 自动获取精确时间，搜索不硬编码年份

### 数据边界界定
- `moderator.py: establish_data_scope()` 在辩论开始前分析议题
- 输出结构化 DataScope: specific_event / time_range / key_entities / relevance_rule
- 例: "哈登和米切尔今天谁表现好" → 事件=5月7日NBA比赛, 时间=2026年5月7日, 规则=只接受该场数据
- 数据边界文本注入每个辩手 prompt，辩手被告知"超出边界的数据不要引用"

### 数据验证
- `data_clerk.py: verify_results()` 搜索结果不是直接入库，先三重验证:
  1. 是否在数据边界范围内 (边界外的排除)
  2. 多条结果是否相互佐证 (矛盾的排除)
  3. 是否与已有数据池一致 (不一致的排除)
- 验证失败(异常)时 fallback 返回全部结果(不阻塞流程)

### 时间感知搜索
- `data_clerk.py: _time_awareness_hint()` 将议题时间语义转为具体日期
- "今天" → "05月07日", "本赛季" → "2025-2026赛季"
- 搜索关键词必须包含议题指向的具体时间

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
| POST | /api/sessions/{id}/clarify | 议题澄清 |
| POST | /api/sessions/{id}/refine | 更新议题 |
| POST | /api/sessions/{id}/suggest-angles | 建议角度 (含 preliminary_data) |
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
| user_data_added | 用户贡献数据 |
| agent_message_start/chunk/complete | 辩手流式发言 |
| score_update | 评分更新 |
| moderator_guidance | 主持人引导 |
| round_complete | 轮次结束 |
| discussion_end | 辩论结束，含纪要 |
| error | 错误 |

## 开发

```bash
# 后端 (需要 Python 3.11+)
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# 前端 (需要 Node 22)
cd frontend
npm install
npm run dev  # http://localhost:5173, 自动代理 /api → localhost:8001

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

## 已修复的关键问题

- 循环导入: models/__init__.py 和 storage/__init__.py 改用延迟导入
- DB会话生命周期: orchestrator 使用独立的 DB session (async_session())，不依赖请求作用域
- SSE unsubscribe: 修复 list.discard → list.remove
- 前端 ScorePanel: 修正 angle_id/angleName 字段映射
- ChatStream processedRef: 两个 useEffect 共享一个 ref 导致消息不显示，拆分为 poolProcessedRef + msgProcessedRef

## 状态

**V1.2 数据边界 + 验证完成**。29/29 测试通过，前端 TS 编译通过。

### 核心特性

- **系统上下文注入**: 每个 agent 自动获取当前精确日期时间，搜索不再硬编码年份
- **数据边界界定**: 主持人在辩论开始前分析议题，确定具体事件/时间/实体/相关性规则，约束所有后续辩论
- **数据验证**: 数据研究员搜索结果先经过三重验证(边界范围/多源佐证/池一致性)才入库
- **时间感知搜索**: 议题中"今天""本赛季"等时间词自动转为具体日期
- **[N] Citation**: 数据引用使用 [1][2] 编号，前端渲染为可点击蓝色 badge + tooltip
- **LLM 结构化输出**: 两层校验 - `json_object` 模式 + Pydantic `model_validate()`
- **共享数据池**: 持久化到 DataPoolItem 表, 按轮次分组, 前端独立面板展示
- **MCP 搜索**: 智谱 ZhipuMCPSearchProvider，复用 LLM API Key，无需单独搜索 key

### 详细设计文档

- [Data Clerk Enhancement Plan](C:\Users\16141\.claude\plans\parallel-crunching-fairy.md) — 数据研究员增强的完整实施计划

### 验证状态
- 后端: 29/29 测试全部通过
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

### 待做 (V2)
- SQLite → PostgreSQL (生产部署)
- 数据池面板移动端优化
- 辩手引用数据的准确性验证 (防幻觉)

### 端到端联调 (V1 MVP 已验证)

使用智谱 GLM-5.1 完成端到端测试:
- LLM 配置: `openai/glm-5.1` + `https://open.bigmodel.cn/api/paas/v4`（必须用 OpenAI 兼容端点，anthropic 兼容端点对中文有编码问题）
- 完整流程: 创建 → 澄清 → 细化 → 角度建议 → 开始讨论 → 纪要生成，全部通过
- 修复了角度 ID 匹配问题: `add_angle()` 需传入 LLM 返回的逻辑 ID，否则 start 时 DB ID 匹配不上
- `.env` 文件需放在 `backend/` 目录下
