# AI 圆桌会议 — 全流程架构图

## 一、整体生命周期

```
用户提交议题
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 0: 议题准备 (session.py: REST API)               │
│  clarify → refine → suggest-positions → start           │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 1: 辩论执行 (orchestrator.py: 后台异步)           │
│  scope → opening → [debate rounds] → minutes            │
└─────────────────────────────────────────────────────────┘
    │
    ▼
  会议纪要 → 前端展示
```

---

## 二、Phase 0: 议题准备（REST API 调用链）

### 0a. 创建议题
```
POST /api/sessions
    │
    ▼
  DB: 创建 Session (status=CREATED)
```

### 0b. 议题澄清 `POST /api/sessions/{id}/clarify`
```
                          ┌─ Moderator.think_before_clarifying(topic, data_context)
                          │   → AgentThinking (LLM complete_typed ×1)
                          │
                          ├─ DataClerk.research_topic(topic, search_provider)
                          │   → ResearchPlan (LLM ×1)
                          │   → 每步: search(q) + 可选 adjust (LLM ×1/step)
                          │   → 返回 raw results
                          │
  session.py              ├─ DataClerk.research_with_validation(topic, provider)
  _run_clarify()  ──────► │   → _scope_filter(results) [规则过滤 ×0 LLM]
                          │   → screen_results(results) [LLM ×1]
                          │   → extract_facts_batch(results) [LLM ×N, semaphore=2]
                          │   → cross_validate_facts(results) [LLM ×1]
                          │   → 可选: 迭代搜索 (max 3轮)
                          │
                          └─ Moderator.clarify_topic(topic, data_context)
                              → ClarifyResult (LLM stream ×1)
```
**SSE 事件**: `search_queries`, `search_results`, `screening_start`, `screening_result`, `validation_complete`, `iterative_search`, `cross_validation_result`

### 0c. 细化议题 `POST /api/sessions/{id}/refine`
```
  session.py  ──────►  更新 session.topic，无 LLM 调用
```

### 0d. 建议角度 `POST /api/sessions/{id}/suggest-positions`
```
                          ┌─ Moderator.think_before_suggesting(topic, data_context)
                          │   → AgentThinking (LLM ×1)
                          │
                          ├─ Moderator.recommend_data_clerk(topic)
                          │   → DataClerkRecommendation (LLM ×1)
                          │
                          ├─ DataClerk.research_for_agent(topic, need, provider)  [如果推荐启用]
  session.py              │   → 5步流水线 (见下文)
  _run_suggest()  ──────► │
                          └─ Moderator.suggest_positions(topic, data_context)
                              → [{name, description}] (LLM stream ×1)
```

### 0e. 用户选择角度 + 开始 `POST /api/sessions/{id}/start`
```
  session.py  ──────►  后台启动 orchestrator.start_discussion()
```

---

## 三、Phase 1: 辩论执行（orchestrator.py 后台流程）

```
start_discussion(session_id, position_ids, enable_data_clerk)
│
├─── 0.5 数据边界界定 [仅 enable_data_clerk=true]
│    │
│    ├── DataClerk.research_topic(topic, search)  → 搜索议题相关事实
│    │     └── _time_awareness_hint() 注入当前日期
│    │
│    └── Moderator.establish_data_scope(topic, positions, data_context)
│          → DataScope { specific_event, time_range, key_entities, relevance_rule }
│          → 生成 data_scope_text (注入后续所有搜索/发言)
│
├─── 1. 开场白
│    └── Moderator.generate_opening(topic, positions)  → LLM stream ×1
│
├─── 2. 辩论轮次 (round_num = 1..max_rounds)
│    │
│    │  ┌─────────────────────────────────────────────────────┐
│    │  │  对每个辩手 PerspectiveAgent:                        │
│    │  │                                                     │
│    │  │  A. 思考 (Two-Pass CoT 第一阶段)                     │
│    │  │     └── agent.think_before_speaking(context, round)  │
│    │  │           → AgentThinking { thinking, data_need,     │
│    │  │              chosen_strategy, my_arguments_standing,  │
│    │  │              opponent_weaknesses, ... }               │
│    │  │           → SSE: agent_thinking                      │
│    │  │           → agent.state.update(think_result)          │
│    │  │                                                     │
│    │  │  B. 数据获取 (Semantic Intent Protocol)               │
│    │  │     如果 data_need 非空:                              │
│    │  │     └── _fetch_and_persist_data()                    │
│    │  │           → SSE: data_fetch_start                    │
│    │  │           → DataClerk.research_for_agent(             │
│    │  │               topic, data_need, provider,             │
│    │  │               pool_summary, data_scope)               │
│    │  │             └── 见 "DataClerk 内部流水线"              │
│    │  │           → DB: persist results to DataPoolItem       │
│    │  │           → SSE: data_fetch_complete                  │
│    │  │                                                     │
│    │  │  C. 重新思考 (Re-Think)                               │
│    │  │     如果获取了新数据:                                  │
│    │  │     └── agent.re_think_with_data(                    │
│    │  │           context, round, fetched_summary, thinking)  │
│    │  │           → AgentThinking (更新后的思考)               │
│    │  │           → SSE: agent_thinking (phase=rethink)       │
│    │  │                                                     │
│    │  │  D. 发言 (流式输出)                                   │
│    │  │     └── agent.stream(context, user_msg)               │
│    │  │           → LLM stream_completion                    │
│    │  │           → SSE: agent_message_start/chunk/complete   │
│    │  │           → DB: persist message                       │
│    │  └─────────────────────────────────────────────────────┘
│    │
│    ├─── 3. 评分
│    │    │
│    │    ├── Scorer.think_before_scoring(topic, round_msgs, positions)
│    │    │     → AgentThinking (LLM ×1)
│    │    │     → SSE: agent_thinking (agent=scorer)
│    │    │
│    │    └── Scorer.score_round(topic, round_msgs, positions)
│    │          → ScoreResult { scores: [{position_id, points, comment}] }
│    │          → SSE: score_update
│    │
│    ├─── 4. 主持人判断
│    │    │
│    │    ├── A. 思考
│    │    │    └── Moderator.think_before_judging(topic, round, max_rounds, summary)
│    │    │          → AgentThinking { thinking, data_need }
│    │    │          → SSE: agent_thinking (agent=moderator)
│    │    │
│    │    ├── B. 数据获取 (同辩手 B 步骤)
│    │    │    如果 data_need 非空:
│    │    │    └── _fetch_and_persist_data()
│    │    │
│    │    ├── C. 重新思考
│    │    │    └── Moderator.re_think_with_data(...)
│    │    │
│    │    └── D. 判断
│    │         └── Moderator.judge_round(topic, round, max_rounds, summary)
│    │               → RoundJudgment { decision: CONTINUE|CONCLUDE, guidance }
│    │               → SSE: moderator_guidance, round_complete
│    │               → 如果 CONCLUDE: break 跳出循环
│    │
│    └─── [回到轮次循环顶部]
│
├─── 5. 生成纪要
│    ├── Moderator.think_before_minutes(topic, positions, all_msgs)
│    │     → AgentThinking (LLM ×1)
│    │     → SSE: agent_thinking (agent=moderator, round=0)
│    │
│    └── Moderator.generate_minutes(topic, positions, all_msgs)
│          → DebateMinutes { core_conclusion, verdict, summary }
│          → SSE: discussion_end
│          → DB: session.status = COMPLETED
│
└─── [异常处理]
     └── session.status = FAILED, SSE: error
```

---

## 四、DataClerk Agent 方法全景

```
DataClerkAgent
│
├─── 对外接口 (被 orchestrator / session 调用)
│    │
│    ├── research_for_agent(topic, semantic_need, provider, ...)
│    │   └── 语义意图协议 (辩论中为辩手/主持人搜索)
│    │       1. Pool sufficiency → PoolSufficiency (LLM ×1)
│    │       2. Need decomposition → NeedDecomposition (LLM ×1)
│    │       3. Search → _scope_filter → screen → extract → validate
│    │       4. 如果 validated==0: 迭代 (max 2 轮)
│    │
│    ├── research_with_validation(topic, provider, ...)
│    │   └── 完整流水线 (Phase 0 议题搜索)
│    │       1. research_topic() → ResearchPlan (LLM ×1)
│    │       2. 逐步搜索 + 调整 (LLM ×1/step)
│    │       3. _scope_filter → screen → extract → validate
│    │       4. 迭代搜索 (max 3 轮)
│    │
│    ├── research_topic(topic, provider)
│    │   └── 分步搜索 (ResearchPlan → 链式执行)
│    │
│    ├── decide_queries(topic, context, position_name, round, ...)
│    │   └── 单次搜索决策 → SearchQueries (LLM ×1)
│    │
│    ├── fetch_for_agent(topic, context, position_name, ...)
│    │   └── decide_queries → parallel search → return
│    │
│    └── fetch_for_topic(topic, provider)
│        └── 单次搜索 → SearchQueries → parallel search
│
├─── 内部流水线步骤
│    │
│    ├── _scope_filter(results, data_scope) [静态方法, 0 LLM]
│    │   └── 规则过滤: 提取 data_scope 中的关键实体
│    │       检查 title+snippet 是否包含至少一个实体
│    │
│    ├── screen_results(results, topic, ...)
│    │   └── LLM 预筛查 → ScreenedResults (LLM ×1, semaphore)
│    │
│    ├── extract_facts(url, query, fallback)
│    │   └── fetch_page_content(url) → LLM 提取 → ExtractedFacts
│    │
│    ├── extract_facts_batch(results, query, max_urls=3)
│    │   └── 并行 extract_facts (semaphore=2, 最多 3 个 URL)
│    │
│    ├── cross_validate_facts(enriched_results, query)
│    │   └── LLM 交叉验证 → CrossValidatedFacts (LLM ×1, semaphore)
│    │
│    └── verify_results(results, topic, data_scope)
│        └── 旧版验证 (兼容保留, 边界+多源+池一致性)
│
└─── 辅助方法
     ├── _time_awareness_hint() → 时间感知提示
     ├── _scope_hint(data_scope) → 数据边界提示
     └── _collect_facts_text(results) → 事实文本提取
```

---

## 五、所有 Agent 方法索引

### BaseAgent (base.py)
| 方法 | LLM | 说明 |
|------|-----|------|
| `think(context, user_msg)` | complete_typed ×1 | 通用思考 → AgentThinking |
| `stream(context, user_msg)` | stream_completion | 流式发言 |
| `respond(context, user_msg)` | complete ×1 | 非流式回复 |
| `respond_typed(model, context, user_msg)` | complete_typed ×1 | 结构化输出 |

### PerspectiveAgent (perspective.py)
| 方法 | LLM | 触发时机 |
|------|-----|----------|
| `think_before_speaking(context, round)` | complete_typed ×1 | 辩论轮次中，辩手发言前 |
| `re_think_with_data(context, round, data, thinking)` | complete_typed ×1 | 获取新数据后，重新思考 |
| `stream(context, user_msg)` | stream | 思考后发言 (继承自 BaseAgent) |

### ModeratorAgent (moderator.py)
| 方法 | LLM | 触发时机 |
|------|-----|----------|
| `think_before_clarifying(topic)` | complete_typed ×1 | Phase 0: 澄清前思考 |
| `clarify_topic(topic)` | stream | Phase 0: 议题澄清 |
| `think_before_suggesting(topic)` | complete_typed ×1 | Phase 0: 建议角度前思考 |
| `suggest_positions(topic)` | stream | Phase 0: 建议辩论角度 |
| `recommend_data_clerk(topic)` | complete_typed ×1 | Phase 0: 是否推荐数据研究员 |
| `generate_opening(topic, positions)` | stream | Phase 1: 开场白 |
| `establish_data_scope(topic, positions)` | complete_typed ×1 | Phase 1: 数据边界界定 |
| `think_before_judging(topic, round, ...)` | complete_typed ×1 | Phase 1: 判断前思考 |
| `re_think_with_data(topic, round, ...)` | complete_typed ×1 | Phase 1: 获取数据后重新思考 |
| `judge_round(topic, round, ...)` | complete_typed ×1 | Phase 1: 轮次判断 |
| `think_before_minutes(topic, positions, ...)` | complete_typed ×1 | Phase 1: 纪要前思考 |
| `generate_minutes(topic, positions, ...)` | complete_typed ×1 | Phase 1: 生成纪要 |

### ScorerAgent (scorer.py)
| 方法 | LLM | 触发时机 |
|------|-----|----------|
| `think_before_scoring(topic, msgs, positions)` | complete_typed ×1 | 每轮评分前思考 |
| `score_round(topic, msgs, positions)` | complete_typed ×1 | 每轮评分 |

### DataClerkAgent (data_clerk.py)
| 方法 | LLM | 触发时机 |
|------|-----|----------|
| `decide_queries(topic, context, ...)` | complete_typed ×1 | 搜索关键词决策 |
| `fetch_for_agent(topic, context, ...)` | decide_queries + search | 单次搜索 (旧接口) |
| `fetch_for_topic(topic, provider)` | complete_typed ×1 + search | 主题搜索 |
| `research_topic(topic, provider)` | complete_typed ×1~4 + search | 分步链式搜索 |
| `research_with_validation(topic, provider, ...)` | 5~15 次 LLM + search | 完整流水线 |
| `research_for_agent(topic, need, provider, ...)` | 4~10 次 LLM + search | 语义意图协议 |
| `_scope_filter(results, scope)` | 0 | 规则过滤 |
| `screen_results(results, topic, ...)` | complete_typed ×1 | LLM 预筛查 |
| `extract_facts(url, query, ...)` | complete_typed ×1 | 页面内容提取 |
| `extract_facts_batch(results, query, ...)` | N×extract_facts | 批量提取 (semaphore=2) |
| `cross_validate_facts(results, query)` | complete_typed ×1 | 交叉验证 |
| `verify_results(results, topic, ...)` | complete_typed ×1 | 旧版验证 (兼容) |

---

## 六、数据流图 (带数据研究员的完整流程)

```
                        ┌──────────────┐
                        │   用户提交    │
                        │   辩论议题    │
                        └──────┬───────┘
                               │
                 ┌─────────────▼──────────────┐
                 │    DataClerk.research_topic  │
                 │    (搜索议题相关事实)          │
                 └─────────────┬──────────────┘
                               │ search_results
                 ┌─────────────▼──────────────┐
                 │   Moderator.clarify_topic    │
                 │   (基于搜索数据审议议题)       │
                 └─────────────┬──────────────┘
                               │ clarified_topic
                 ┌─────────────▼──────────────┐
                 │   DataClerk.research_with_   │
                 │   validation (补充搜索)       │
                 └─────────────┬──────────────┘
                               │ preliminary_data
                 ┌─────────────▼──────────────┐
                 │   Moderator.suggest_positions│
                 │   (建议角度+推荐数据研究员)    │
                 └─────────────┬──────────────┘
                               │ selected positions
                 ┌─────────────▼──────────────┐
                 │ 用户选择角度,点击开始讨论      │
                 └─────────────┬──────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │         Orchestrator.start_discussion     │
          │              (后台异步执行)                │
          └────────────────────┬────────────────────┘
                               │
            ┌──────────────────▼──────────────────┐
            │  DataClerk.research_topic → 搜索事实  │
            │  Moderator.establish_data_scope       │
            │  → DataScope { 事件/时间/实体/规则 }   │
            └──────────────────┬──────────────────┘
                               │ data_scope_text
            ┌──────────────────▼──────────────────┐
            │  Moderator.generate_opening (开场白)  │
            └──────────────────┬──────────────────┘
                               │
            ╔══════════════════▼══════════════════╗
            ║     辩论轮次 (round 1..N)             ║
            ║                                      ║
            ║  对每个辩手:                           ║
            ║    ① think_before_speaking → thinking ║
            ║    ② data_need非空? → research_for_   ║
            ║       agent → scope_filter → screen   ║
            ║       → extract → validate            ║
            ║    ③ re_think_with_data (如有新数据)   ║
            ║    ④ stream (流式发言)                 ║
            ║                                      ║
            ║  Scorer:                              ║
            ║    ① think_before_scoring → thinking  ║
            ║    ② score_round → ScoreResult        ║
            ║                                      ║
            ║  Moderator:                           ║
            ║    ① think_before_judging → thinking  ║
            ║    ② data_need? → fetch + re_think    ║
            ║    ③ judge_round → CONTINUE|CONCLUDE  ║
            ║                                      ║
            ║  如果 CONCLUDE → 跳出循环              ║
            ╚══════════════════╤═══════════════════╝
                               │
            ┌──────────────────▼──────────────────┐
            │  Moderator.generate_minutes (纪要)    │
            │  → DebateMinutes { 结论, 裁决, 摘要 } │
            └─────────────────────────────────────┘
```

---

## 七、LLM 调用预算 (每轮辩论, 2 个辩手, enable_data_clerk=true, enable_cot=true)

| 步骤 | LLM 调用次数 | 说明 |
|------|-------------|------|
| 辩手1 思考 | 1 | think_before_speaking |
| 辩手1 数据获取 | 0~10 | research_for_agent (pool_check + decomposition + screen + 3×extract + validate) |
| 辩手1 重新思考 | 0~1 | re_think_with_data |
| 辩手1 发言 | 1 | stream_completion |
| 辩手2 (同上) | 0~13 | |
| 评委思考 | 1 | think_before_scoring |
| 评委评分 | 1 | score_round |
| 主持人思考 | 1 | think_before_judging |
| 主持人数据获取 | 0~10 | research_for_agent |
| 主持人重新思考 | 0~1 | re_think_with_data |
| 主持人判断 | 1 | judge_round |
| **每轮合计** | **5~50** | 取决于数据需求 |

**纪要生成**: 2 次 (think_before_minutes + generate_minutes)

---

## 八、关键配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ENABLE_COT` | true | 两阶段思考开关 (false=跳过所有 think_before_xxx) |
| `SEARCH_PROVIDER` | "" | 搜索提供商 (zhipu), 留空禁用搜索 |
| `DATA_CLERK_MODEL` | 跟随 LLM_MODEL | 数据研究员专用模型 |
| `LLM_MODEL` | openai/glm-5.1 | 全局 LLM 模型 |

---

## 九、SSE 事件时序 (一轮辩论, 2辩手)

```
→ round_start {round: 1}

→ agent_thinking {agent: "pos_1", thinking: "..."}          # 辩手1思考
→ data_fetch_start {agent: "pos_1", data_need: "..."}       # 辩手1需要数据
→ search_queries {queries: ["..."]}
→ search_results {results: [...]}
→ data_fetch_complete {results: [...]}
→ agent_thinking {agent: "pos_1", phase: "rethink"}         # 辩手1重新思考
→ agent_message_start {agent: "pos_1"}
→ agent_message_chunk {chunk: "..."}                          # 流式发言
→ agent_message_complete {content: "..."}

→ agent_thinking {agent: "pos_2", thinking: "..."}          # 辩手2 (无数据需求)
→ agent_message_start {agent: "pos_2"}
→ agent_message_chunk {chunk: "..."}
→ agent_message_complete {content: "..."}

→ agent_thinking {agent: "scorer", thinking: "..."}         # 评委思考
→ score_update {scores: [...]}

→ agent_thinking {agent: "moderator", thinking: "..."}      # 主持人思考
→ moderator_guidance {content: "...", round: 1}
→ round_complete {round: 1, decision: "CONTINUE"}
```

---

## 十、数据边界过滤 (三层防护)

```
搜索引擎返回结果
       │
       ▼
  ① _scope_filter()         ← 规则匹配 (0 LLM)
     提取 data_scope 中的关键实体
     检查 title+snippet 是否包含实体
     不匹配 → 直接丢弃
       │
       ▼
  ② screen_results()        ← LLM 筛查 (1 LLM)
     判断相关性/矛盾/重复
     语义层面的精细过滤
       │
       ▼
  ③ cross_validate_facts()  ← LLM 交叉验证 (1 LLM)
     多源佐证/矛盾检测
     validated/unique/contradictions
       │
       ▼
  加入数据池 → DB + public_data_pool
```

---

## 十一、辩手状态积累 (DebaterState)

```
每轮思考后更新:
  ┌─────────────────────────────────────────┐
  │  DebaterState                           │
  │  ├── my_arguments_standing: []    仍站得住的论点 │
  │  ├── my_arguments_refuted: []     被反驳的论点   │
  │  ├── opponent_weaknesses: []      对手弱点       │
  │  └── strategy_history: []         策略历史       │
  │                                         │
  │  容量: 每个列表上限 20 条                   │
  │  连续相同策略 ≥3 轮时标记                   │
  │  自动注入辩手系统提示                       │
  └─────────────────────────────────────────┘
```
