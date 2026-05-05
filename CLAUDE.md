# AI 圆桌会议

多角度 AI 协作讨论平台。用户提交议题，多个 AI agent 从不同角度讨论，产出结构化会议纪要。

## 架构

- **后端**: Python FastAPI + SQLite + LiteLLM
- **前端**: React + Vite + TailwindCSS v4
- **实时通信**: SSE (Server-Sent Events)
- **部署**: Docker Compose

## 项目结构

```
backend/
  app/
    main.py          # FastAPI 入口 (14 个路由)
    config.py        # pydantic-settings 环境变量
    routers/
      session.py     # REST API (CRUD + 讨论控制)
      sse.py         # SSE 实时事件流 (EventSource)
    services/
      orchestrator.py # 讨论流程编排 (后端独立运行，断线不中断)
      llm.py         # LiteLLM acompletion(stream=True) token级流式
      search.py      # 多提供商搜索抽象层 (Zhipu/Tavily/None)
    agents/
      base.py        # Agent 基类 (stream/respond/respond_json)
      moderator.py   # 主持人 (审议/建议/引导/纪要)
      perspective.py # 角度嘉宾 (认输机制)
      scorer.py      # 评分 (独立0-100, 三维度)
      prompts/       # Prompt 模板 (.md 文件)
    models/
      session.py     # SQLAlchemy 异步模型 (Session/Angle/Message)
    storage/
      database.py    # async SQLAlchemy engine
      repository.py  # 数据访问层
  tests/
    test_session_api.py    # API 端点测试 (7 tests)
    test_repository.py     # 数据层测试 (7 tests)
    test_orchestrator.py   # 编排器集成测试 (1 test, mock LLM)
frontend/
  src/
    pages/           # Home, Angles, Discussion, Minutes
    components/      # ChatStream (token级流式渲染), ScorePanel (实时评分)
    hooks/           # useSSE (自动重连)
    lib/             # API 客户端
```

## 讨论流程

1. 用户提交议题 → 主持人审议澄清
2. 主持人建议角度 → 用户选择 2-6 个
3. 主持人开场 → 循环: 嘉宾依次发言 → 评分 → 主持人判断
4. 主持人生成会议纪要

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
| POST | /api/sessions/{id}/suggest-angles | 建议角度 |
| POST | /api/sessions/{id}/start | 开始讨论 |
| GET | /api/sessions/{id}/stream | SSE 事件流 |
| GET | /api/health | 健康检查 |

## 开发

```bash
# 后端 (需要 Python 3.11+)
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# 前端 (需要 Node 22)
cd frontend
npm install
npm run dev  # http://localhost:5173, 自动代理 /api → localhost:8000

# 测试
cd backend && python -m pytest tests/ -v

# Docker
docker-compose up --build
```

## 环境变量

复制 `.env.example` 为 `.env`，填入 LLM API key。

| 变量 | 必需 | 说明 |
|------|------|------|
| LLM_API_KEY | 是 | LLM API 密钥 |
| LLM_MODEL | 否 | 默认 glm-5.1 |
| LLM_BASE_URL | 否 | 默认智谱 Anthropic 兼容端点 |
| SEARCH_PROVIDER | 否 | zhipu / tavily，留空禁用搜索 |

## 已修复的关键问题

- 循环导入: models/__init__.py 和 storage/__init__.py 改用延迟导入
- DB会话生命周期: orchestrator 使用独立的 DB session (async_session())，不依赖请求作用域
- SSE unsubscribe: 修复 list.discard → list.remove
- 前端 ScorePanel: 修正 angle_id/angleName 字段映射

## 状态

**V1 MVP 完成**。6 个 commits，22/22 测试通过，前端 TS 编译 + build 通过。

### 验证通过
- 后端: 22/22 测试全部通过 (API 7 + Repository 7 + Orchestrator 1 + SSE 7)
- 前端: TypeScript strict mode 编译通过，Vite build 成功 (251KB JS + 18KB CSS)
- API 冒烟测试: health / create / list / get 全部正常
- 前端审查: 6 项问题已修复 (移动端布局、错误状态、类型安全)

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

### 待做 (V1.1+)
- 填入真实 API key 做端到端联调 (需要 LLM_API_KEY)
- SQLite → PostgreSQL (生产部署)
- 搜索功能实际联调 (Zhipu MCP 或 Tavily)
