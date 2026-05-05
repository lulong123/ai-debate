# AI 圆桌会议

多角度 AI 协作讨论平台。提交一个议题，多个 AI agent 从不同角度协作分析，帮你做出更好的判断。

## 为什么做这个

AI 只能给你一个角度的回答，容易自圆其说、遗漏盲点。这个工具让多个 AI 角色从不同维度分析同一个问题，协作产出全面的结论和可落地方案。

跟 AI 辩论工具不同：不是打架，是协作。目标不是"谁赢了"，而是帮你做出更好的判断。

## 功能

- 提交议题，AI 主持人审议并建议讨论角度
- 选择 2-6 个角度（技术、法律、伦理、经济...）
- 实时观看各角度 agent 依次发言（token 级流式）
- 每轮结束后实时评分
- 讨论结束自动生成结构化会议纪要
- 纪要导出为 Markdown

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 22+
- LLM API Key（智谱 / OpenAI 兼容）

### 后端

```bash
cd backend
pip install -e ".[dev]"

# 配置环境变量
cp ../.env.example ../.env
# 编辑 .env 填入 LLM_API_KEY

uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
# 打开 http://localhost:5173
```

### Docker

```bash
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY

docker-compose up --build
# 打开 http://localhost:3000
```

## 讨论流程

```
用户提交议题
    ↓
主持人审议（可能追问澄清）
    ↓
主持人建议 3-5 个讨论角度
    ↓
用户选择角度（至少 2 个）
    ↓
循环 1-N 轮：
  各角度 agent 依次发言
  评分 agent 打分
  主持人判断是否继续
    ↓
主持人生成会议纪要
```

## 成本

默认配置（3 个角度，3 轮）：约 31 次 LLM 调用，30k-50k tokens。
- 智谱 GLM-4：约 ¥0.1-0.3/场
- GPT-4o-mini：约 $0.02-0.05/场

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | FastAPI + SQLAlchemy + LiteLLM |
| 前端 | React + Vite + TailwindCSS |
| 实时通信 | SSE (Server-Sent Events) |
| 数据库 | SQLite (MVP) / PostgreSQL (生产) |
| 部署 | Docker Compose |

## 项目结构

```
backend/
  app/
    main.py              # FastAPI 入口
    config.py            # 环境变量配置
    routers/
      session.py         # REST API
      sse.py             # SSE 实时流
    services/
      orchestrator.py    # 讨论编排（后端独立运行）
      llm.py             # LiteLLM 异步流式
      search.py          # 多提供商搜索
    agents/
      base.py            # Agent 基类
      moderator.py       # 主持人
      perspective.py     # 角度嘉宾
      scorer.py          # 评分
      prompts/           # Prompt 模板
    models/session.py    # 数据模型
    storage/             # 持久化层
  tests/                 # 15 个测试
frontend/
  src/
    pages/               # 4 个页面
    components/          # ChatStream, ScorePanel
    hooks/               # useSSE
    lib/                 # API 客户端
```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `LLM_API_KEY` | 是 | LLM API 密钥 |
| `LLM_MODEL` | 否 | 默认 `glm-5.1` |
| `LLM_BASE_URL` | 否 | 默认智谱 API |
| `SEARCH_PROVIDER` | 否 | `zhipu` / `tavily`，留空禁用 |

## License

MIT
