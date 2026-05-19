# DataClerkAgent 全景思维导图

## 一、总览：两条研究管线

```
DataClerkAgent
├── 管线A: research_with_validation()  ← 面向议题（clarify/suggest阶段）
│   目的: 对整个议题做全面研究，数据进公共数据池
│   调用者: session.py clarify + suggest 流程
│
└── 管线B: research_for_agent()        ← 面向辩手/主持人（辩论中）
    目的: 针对某个agent的特定数据需求做定向研究
    调用者: orchestrator.py 每轮辩论前的数据搜索
```

## 二、管线A: research_with_validation（完整流程）

```
research_with_validation(topic, search_provider, ...)
│
├── 1. _decide_recency(topic)
│   ├── 目的: LLM判断搜索需要多新的数据
│   ├── 输出: "oneDay" / "oneWeek" / "oneMonth" / "noLimit"
│   ├── 策略: 体育赛事→oneMonth, "今天"→oneDay, 历史→noLimit
│   └── 容错: 失败默认 noLimit
│
├── 2. _decompose_topic(topic) → TopicDecomposition
│   ├── 目的: LLM提取议题中的实体+隐藏子问题
│   ├── 输出: entities（含别名）+ hidden_sub_topics + search_strategy_hint
│   ├── 示例: "詹姆斯上一场" → 实体=[詹姆斯/LeBron], 子问题="上一场是哪场？"
│   └── 容错: 失败返回空 TopicDecomposition（不阻塞）
│
├── 3. 研究计划生成 (LLM → ResearchPlan)
│   ├── 目的: 制定分步搜索计划
│   ├── 输入: topic + decomposition结果 + 时间感知
│   ├── 输出: ResearchPlan.steps[] 每步含 reasoning + search_queries
│   ├── 第1步: 4个关键词（宽泛覆盖：中+英+数据+动态）
│   ├── 第2-3步: 每步2个关键词（基于前步发现调整）
│   └── 要求: 至少3步，关键词互不重复
│
├── 4. 逐步执行 (最多 max_steps=5 轮)
│   │
│   ├── 4a. 第1步: 直接执行 plan.steps[0] 的 queries
│   │
│   ├── 4b. 第2步+: LLM动态调整 (→ ResearchStep)
│   │   ├── 目的: 基于前步搜索结果调整当步关键词
│   │   ├── 输入: 前步搜索结果 + 已积累发现 + 时间感知
│   │   ├── 输出: adjusted.search_queries + discoveries + resolved_sub_topic
│   │   ├── 机制: "事实锁定" — resolved 后续步骤不得用旧数据覆盖
│   │   └── 容错: LLM失败时回退用原始 plan 的 queries
│   │
│   ├── 4c. 每步执行搜索
│   │   ├── search_provider.search(q, max_results=3, recency)
│   │   ├── _statmuse_query(q) → 体育/金融补充分辨
│   │   └── _fetch_top_results_content(results, max_fetch=2)
│   │       ├── 目的: 为下步分析获取页面摘要
│   │       └── 通过 fetch_page_content() 获取前2个结果的页面内容
│   │
│   └── 4d. on_search 回调通知前端（SSE: data_fetch_start/complete）
│
├── 5. _supplementary_search()（补充搜索）
│   ├── 目的: 如果初始搜索结果不够多，从多角度补充
│   ├── 策略: 固定角度生成多样化关键词（最新/数据/新闻/latest/stats/news）
│   ├── 去重: 跳过与已有结果高度重叠的关键词
│   └── 触发条件: 初始搜索后总结果数不足
│
├── 6. _scope_filter(results, data_scope) [规则过滤，零LLM开销]
│   ├── 目的: 基于数据边界文本，规则过滤明显不相关的结果
│   ├── 机制: 从 data_scope 提取"关键实体"，检查每条结果是否提及
│   ├── 示例: 边界="湖人对雷霆" → 结果提"火箭"则过滤
│   ├── 特殊处理: "X对Y"格式拆分，检查 X OR Y
│   └── 空边界时: 不过滤，全部通过
│
├── 7. screen_results(results, topic, ...) [LLM预筛]
│   ├── 目的: 仅凭标题+摘要排除不相关结果，节省web_reader开销
│   ├── 特殊: StatMuse结果自动保留（bypass screening）
│   ├── URL恢复: LLM返回的kept结果用标题匹配回原始结果（恢复URL等字段）
│   ├── 筛查维度:
│   │   ├── 议题相关性
│   │   ├── 已知信息矛盾
│   │   ├── 重复检测
│   │   ├── 数据边界匹配
│   │   └── **时间和对手匹配**（核心！不同比赛/系列赛必须排除）
│   ├── 可选上下文: data_scope / pool_summary / semantic_need / existing_context
│   └── 容错: 失败返回全部结果
│
├── 8. extract_facts_batch(screened, topic) [事实提取]
│   ├── 目的: 对筛查通过的结果，提取结构化事实
│   ├── 并发控制: _llm_semaphore(2) 限制并发LLM调用
│   ├── 单条处理: extract_facts(url, query, fallback_content, topic)
│   │   ├── 页面获取: fetch_page_content(url) → Zhipu MCP Web Reader
│   │   ├── SPA检测: 页面<300字符时回退用搜索snippet
│   │   ├── LLM提取: 从页面内容提取客观事实（≤8条，每条≤100字）
│   │   ├── topic_hint: 提示提取范围（主体+对手+队友+系列赛背景）
│   │   ├── 输出: {"key_facts": [...], "summary": "..."}
│   │   └── 容错: 失败时用内容前200字符作为单条事实
│   └── 输出: enriched_results（每条结果增加了 key_facts 字段）
│
├── 9. cross_validate_facts(enriched_results, topic) [交叉验证]
│   ├── 目的: 对比多源提取的事实，判断可信度
│   ├── 输入: 所有结果的 key_facts（按来源分组展示给LLM）
│   ├── LLM对比输出:
│   │   ├── validated: 2+来源佐证的事实（附source_count）→ 高置信度
│   │   ├── unique: 仅单一来源的事实 → 中置信度
│   │   └── contradictions: 各来源矛盾的事实 → 低置信度
│   └── 容错: 失败返回空 CrossValidatedFacts
│
├── 10. 迭代补充搜索（最多 max_iterations=3 轮）
│   ├── 退出条件: validated >= 2（佐证足够）
│   ├── LLM生成补充关键词 (→ RefinementQueries)
│   │   ├── 输入: validated facts + contradictions + unique facts
│   │   ├── 目的: 针对单源事实生成精确搜索关键词寻找佐证
│   │   └── 示例: "爱德华兹36分" → 搜索"爱德华兹 36分 战报"
│   ├── 搜索 → scope_filter → screen → extract → 合并去重 → 重新验证
│   └── 去重: 按URL去重，已搜过的query跳过
│
├── 11. 质量标注
│   ├── "high": validated >= 2
│   ├── "medium": validated >= 1
│   └── "low": validated == 0
│
├── 12. _map_validated_to_results(enriched_results, validation) [入池决策]
│   ├── 目的: 决定哪些结果进公共数据池（is_public=True）
│   ├── 条件1: validated 为空 → 只有 StatMuse 进公共池
│   ├── 条件2: validated >= 1 → 所有结果进公共池
│   └── 输出: (public_results, private_results)
│
└── 13. 返回 ResearchOutcome
    ├── public_results: 公开数据（前端可见，辩手可用）
    ├── private_results: 私有数据（agent可见，前端不可见）
    └── validation: 交叉验证结果（用于SSE通知前端）
```

## 三、管线B: research_for_agent（定向研究）

```
research_for_agent(topic, semantic_need, search_provider, ...)
│
├── 0. recency判断（auto时调用 _decide_recency）
│
├── 1. PoolSufficiency检查
│   ├── 目的: 数据池是否已满足agent需求？满足则跳过搜索
│   ├── 输入: pool_summary + full_research_context（含私有数据）
│   ├── LLM输出: sufficient=true/false + reasoning
│   └── sufficient=true → 直接返回空 ResearchOutcome
│
├── 2. 迭代搜索循环（最多 max_iterations=2 轮）
│   │
│   ├── 2a. NeedDecomposition (LLM → NeedDecomposition)
│   │   ├── 目的: 将语义需求转化为1-2个搜索关键词
│   │   ├── sufficiency字段: LLM显式判断是否已有足够数据
│   │   ├── sufficient=true且无queries → 直接返回
│   │   ├── 查询去重: 跳过 session 历史中已搜过的关键词
│   │   └── 回退: 空queries时用topic提取实体生成回退关键词
│   │
│   ├── 2b. 搜索 → scope_filter → screen_results → extract_facts_batch
│   │   └── 复用管线A的同一套过滤和提取逻辑
│   │
│   ├── 2c. cross_validate_facts（验证）
│   │
│   ├── 2d. _detect_data_gap() [数据缺口检测]
│   │   ├── 目的: 新发现的unique事实是否揭示数据池遗漏？
│   │   ├── 示例: 池只有欧冠数据，新事实涉及英超 → has_gap=true
│   │   └── 补充搜索 → scope_filter → screen → extract → 重新验证
│   │
│   └── 2e. 提前退出: validated >= 2
│
├── 3. _check_data_sufficiency() + _filter_by_relevance()
│   ├── 目的: 最终过滤，只保留与agent需求直接相关的结果
│   ├── LLM判断: 哪些事实直接回答了 semantic_need
│   └── 关键词匹配: 只保留 key_facts 含 relevant_facts 关键词的结果
│
├── 4. _map_validated_to_results()（同管线A）
│
└── 5. 返回 ResearchOutcome
```

## 四、辅助搜索通道

```
StatMuse（体育/金融专业数据）
├── _statmuse_query(query, provider)
│   ├── 目的: 从StatMuse获取权威体育/金融数据
│   ├── 分类: _classify() 判断 query 属于 nba/fc/nfl/mlb/money 还是无关
│   ├── URL构建: _build_url() 规范化查询为StatMuse URL
│   ├── 页面解析: _parse_html() 提取 og:description + 表格数据
│   ├── 缓存: LRU缓存（≤100条），避免重复请求
│   └── 结果标记: source="statmuse"
│
├── StatMuse 信任机制:
│   ├── screen_results: StatMuse自动保留，bypass LLM筛查
│   ├── _map_validated_to_results: 无验证时只有StatMuse进公共池
│   └── 原因: 专业数据网站，结构化准确度高
│
└── StatMuse限制:
    ├── 仅英文
    ├── 仅覆盖 nba/fc/nfl/mlb/money
    └── 422错误时不阻塞（常见于不支持的问题）
```

## 五、数据入池与持久化

```
session.py → persist_research_results()
│
├── 输入: all_results = public + private
├── public_urls = {r.url for r in public_results}
│
├── repository.persist_research_results()
│   ├── URL去重: 已存在的URL跳过（Skipping duplicate pool item）
│   ├── is_public判定: url in public_urls → True/False
│   └── 写入 DataPoolItem 表（SQLite）
│       ├── session_id / source / title / snippet / url
│       ├── key_facts (JSON字符串)
│       ├── is_public (bool)
│       ├── round_number / publish_date / citation_num
│       └── created_at
│
├── 前端可见: is_public=True 的条目
├── Agent可见: 全部条目（公开+私有）
│
└── 数据池消费:
    ├── get_pool_summary() → 格式化公开池摘要（注入agent prompt）
    ├── [N] citation系统（citation_num编号）
    └── 用户贡献: POST /api/sessions/{id}/data-pool（用户手动添加数据）
```

## 六、置信度体系

```
交叉验证结果:
├── validated (source_count >= 2) → 高置信度
│   └── 含义: 多个独立来源佐证的事实
│
├── unique (source_count = 1) → 中置信度
│   └── 含义: 仅单一来源，未经佐证
│
├── contradictions → 低置信度
│   └── 含义: 不同来源的矛盾信息
│
质量标注:
├── "high": validated >= 2 → 可信
├── "medium": validated >= 1 → 基本可信
└── "low": validated == 0 → 需谨慎

入池条件:
├── validated >= 1 → 全部结果进公共池（当前实现）
├── validated == 0 → 仅 StatMuse 进公共池
└── 任何情况 → 私有池始终可用（agent可参考但前端不可见）
```

## 七、辩论各阶段的数据流

```
阶段1: clarify（主持人审议议题）
├── 触发: POST /api/sessions/{id}/clarify
├── 管线: research_with_validation()
├── data_scope: 空（边界尚未建立）
├── 结果: 公开数据进池 → 作为主持人审议的参考
└── 主持人思考后可能触发 followup search: research_for_agent()

阶段2: suggest（主持人建议角度）
├── 触发: POST /api/sessions/{id}/suggest-positions
├── 管线: research_with_validation()
├── data_scope: 空（边界尚未建立）
├── 结果: 公开数据进池 → preliminary_data 注入角度建议
└── 主持人思考后可能触发 followup search: research_for_agent()

阶段3: start（主持人建立数据边界）
├── 触发: POST /api/sessions/{id}/start
├── moderator.establish_data_scope()
│   ├── 目的: 分析议题+已有数据，确定数据边界
│   └── 输出: DataScope {specific_event, time_range, key_entities, relevance_rule}
├── data_scope 注入后续所有搜索（scope_filter + screen_results）
└── 数据边界约束每个辩手的发言

阶段4: 辩论轮次（每轮循环）
├── 4a. 为每位辩手搜索: research_for_agent()
│   ├── 输入: semantic_need = 辩手的数据需求（从思考中提取）
│   ├── data_scope: 已建立 → scope_filter生效
│   ├── pool_summary: 已有数据 → PoolSufficiency检查
│   └── 结果: 新数据入池
│
├── 4b. 辩手思考: think_before_speaking()
│   ├── 注入: 数据池 + 数据边界
│   └── 可触发: data_requests → 回到4a补充搜索 → 重新思考
│
├── 4c. 辩手发言: respond()（流式SSE）
│   ├── 注入: 完整数据池（强制引用[N]编号）
│   └── 约束: 遵守数据边界，超出边界的数据不引用
│
├── 4d. 评委评分: think_before_scoring() + score
│   └── 评分维度含: 数据运用合理性
│
├── 4e. 主持人判断: think_before_judging() + continue/end
│   └── 可触发: data_requests → 回到4a补充搜索 → 重新判断
│
└── 4f. 用户贡献: POST /data-pool → 手动添加数据
```

## 八、并发控制与容错

```
_llm_semaphore = asyncio.Semaphore(2)
├── 限制同时LLM调用数，避免API限流
└── 作用于: extract_facts_batch / cross_validate / screen_results

搜索限流重试:
├── 429错误 → sleep(1) 重试一次
└── _safe_single_search / _safe_search 封装

容错原则:
├── 所有LLM调用都有 try/except
├── 失败不阻塞主流程
├── 回退策略:
│   ├── _decompose_topic 失败 → 空分解
│   ├── screen_results 失败 → 返回全部
│   ├── extract_facts 失败 → 用原始内容做单条事实
│   ├── cross_validate 失败 → 空验证
│   └── verify_results 失败 → 返回全部
└── Session级query去重: searched_queries 集合防止重复搜索

常量:
├── MAX_QUERIES = 2 (每步关键词上限)
├── FIRST_STEP_QUERIES = 4 (第一步宽泛覆盖)
├── MAX_TOTAL_RESULTS = 10 (fetch_for_agent上限)
├── MAX_EXTRACT_URLS = 10 (事实提取URL上限)
└── 缓存: StatMuse LRU(100)
```

## 九、核心数据流路径（总结）

```
搜索 → scope_filter(规则) → screen_results(LLM) → extract_facts(LLM) → cross_validate(LLM) → _map_validated_to_results(规则) → persist(URL去重)
```

每一层的过滤目的：
1. **scope_filter**: 零成本，排除完全不沾边的结果（如边界说湖人vs雷霆，结果提火箭）
2. **screen_results**: LLM低成本（只看标题摘要），排除不相关/矛盾/不同比赛的结果
3. **extract_facts**: LLM中等成本（读页面内容），从杂乱的HTML提取结构化事实
4. **cross_validate**: LLM中等成本，多源对比判断可信度
5. **_map_validated_to_results**: 零成本规则，基于验证结果决定公开/私有
6. **persist**: 零成本，URL去重防重复入库
