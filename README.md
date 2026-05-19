# AI 圆桌会议 / AI Roundtable

多角度 AI 协作讨论平台。提交一个议题，多个 AI agent 从不同角度协作分析，实时搜索验证数据，产出结构化会议纪要。

AI 只能给你一个角度的回答，容易自圆其说、遗漏盲点。这个工具让多个 AI 角色从不同维度分析同一个问题，协作产出全面的结论。跟辩论工具不同：不是打架，是协作——目标不是"谁赢了"，而是帮你做出更好的判断。

## Features

- **多 Agent 协作辩论** — 主持人、辩手（可配置角度）、数据研究员、评委，结构化多轮讨论
- **实时数据研究** — 数据研究员自动搜索、预筛查、提取、交叉验证网页数据，佐证不足时迭代补充搜索
- **两阶段思考 (CoT)** — 每个 agent 行动前先做结构化思考，思考过程前端可见（可折叠面板）
- **多模型路由** — YAML 配置不同 agent 使用不同 LLM（GLM / GPT / Claude / DeepSeek / Gemini / Qwen / 本地模型等）
- **[N] 引用系统** — 所有观点基于编号引用，点击可查看来源标题、摘要、URL
- **SSE 流式传输** — Token 级实时流式渲染，断线自动重连
- **共享数据池** — 用户可在辩论过程中向数据池添加自己的数据
- **辩手跨轮次记忆** — 辩手记住哪些论点被反驳、对手弱点，不重复失败论点

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy (async), SQLite |
| Frontend | React 19, Vite 6, TailwindCSS v4, TypeScript |
| LLM | LiteLLM (OpenAI-compatible, 20+ providers) |
| Real-time | SSE (Server-Sent Events) |
| Search | Zhipu MCP Search (reuses LLM API key) |
| Structured Output | `response_format=json_object` + Pydantic validation |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 22+
- An LLM API key (e.g. [Zhipu/GLM](https://open.bigmodel.cn))

### 1. Backend

```bash
cd backend
cp ../.env.example .env
# Edit .env, fill in LLM_API_KEY
pip install -e ".[dev]"
python -m uvicorn app.main:app --port 8002
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173 (auto-proxies /api → localhost:8002)
```

### 3. Docker

```bash
cp .env.example .env
# Edit .env, fill in LLM_API_KEY
docker-compose up --build
# Open http://localhost:3000
```

## Configuration

### Single Model (default)

Set in `.env`:

```env
LLM_MODEL=openai/glm-5.1
LLM_API_KEY=your_key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

### Multi-Model Routing

```bash
cp backend/config.example.yaml backend/config.yaml
```

Edit `config.yaml` to assign different models per agent role:

```yaml
models:
  - role: default
    model: openai/glm-5.1
    api_key: $LLM_API_KEY
    base_url: https://open.bigmodel.cn/api/paas/v4

  - role: debater
    model: openai/gpt-4o-mini
    api_key: $OPENAI_API_KEY

  - role: data_clerk
    model: deepseek/deepseek-chat
    api_key: $DEEPSEEK_API_KEY
```

Supported roles: `default`, `moderator`, `debater`, `scorer`, `data_clerk`

See [backend/config.example.yaml](backend/config.example.yaml) for all providers: GLM, OpenAI, Claude, DeepSeek, Gemini, Qwen, Moonshot, Ollama, Volcengine, etc.

## Debate Flow

```
User submits topic
    ↓
Data clerk searches topic context → Moderator clarifies (with search data)
    ↓
Moderator suggests debate angles → User selects 2-6 angles
    ↓
Moderator establishes data boundaries (event, time, entities, relevance rules)
    ↓
Loop 1-N rounds:
  Data clerk: time-aware search → screen → extract → cross-validate
              (iterative search if validation insufficient, up to 3 rounds)
  Debaters:   think (see data pool + boundaries) → speak (cite [1][2]...)
  Moderator:  think → judge (continue / conclude)
    ↓
Moderator generates structured meeting minutes
```

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/sessions` | Create discussion |
| GET | `/api/sessions` | List discussions |
| GET | `/api/sessions/{id}` | Get details |
| POST | `/api/sessions/{id}/clarify` | Clarify topic |
| POST | `/api/sessions/{id}/refine` | Refine topic |
| POST | `/api/sessions/{id}/suggest-positions` | Suggest debate angles |
| POST | `/api/sessions/{id}/start` | Start debate |
| POST | `/api/sessions/{id}/data-pool` | Add user data |
| GET | `/api/sessions/{id}/stream` | SSE event stream |
| GET | `/api/sessions/{id}/minutes` | Get meeting minutes |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_API_KEY` | Yes | LLM API key (also used for search) |
| `LLM_MODEL` | No | Default model (`openai/glm-5.1`) |
| `LLM_BASE_URL` | No | API endpoint (default: Zhipu) |
| `SEARCH_PROVIDER` | No | `zhipu` to enable, empty to disable |
| `ENABLE_COT` | No | Chain-of-thought toggle (`true` by default) |

## Project Structure

```
backend/
  app/
    main.py              # FastAPI entry
    config.py            # pydantic-settings + YAML model routing
    routers/
      session.py         # REST API (CRUD + debate control + data pool)
      sse.py             # SSE real-time events
    services/
      orchestrator.py    # Debate orchestration (runs independently)
      llm.py             # LiteLLM acompletion + structured output
      search.py          # Zhipu MCP search
    agents/
      base.py            # Agent base (system context + DebaterState)
      moderator.py       # Moderator (clarify/suggest/boundaries/guide/minutes)
      perspective.py     # Debater (mandatory citations + thinking + state)
      data_clerk.py      # Data researcher (search/screen/extract/validate)
      prompts/           # Prompt templates (.md)
    models/
      session.py         # SQLAlchemy async models
      schemas.py         # Pydantic structured output schemas
    storage/
      database.py        # Async engine + idempotent migrations
      repository.py      # Data access layer
  tests/                 # 152 tests
frontend/
  src/
    pages/               # Home, Positions, Discussion, Minutes
    components/
      ChatStream.tsx     # Token-level streaming + [N] citations + thinking panels
      DataPoolPanel.tsx  # Shared data pool (grouped by round)
      SessionCard.tsx    # Session list card
    hooks/
      useSSE.ts          # SSE auto-reconnect with lastEventId replay
    lib/
      api.ts             # Typed API client
```

## Testing

```bash
cd backend
python -m pytest tests/ -v
```

## Cost Estimate

Default config (3 angles, 3 rounds): ~31 LLM calls, 30k-50k tokens per debate.

| Provider | Cost/Debate |
|----------|-------------|
| Zhipu GLM-5.1 | ~¥0.1-0.3 |
| GPT-4o-mini | ~$0.02-0.05 |

## License

MIT
