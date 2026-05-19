# Engineering Plan: JRPG 对话模式 + 回看模式

## 变更概述

将 Discussion 页面从"聊天室文本流"改为日式 RPG 对话模式。角色发言时全屏展示立绘 + 打字机文字；可切换到"回看模式"查看完整历史记录。

**不新增独立路由**。直接改造 Discussion.tsx，通过模式切换实现。

## Constraint

- 不修改后端任何文件
- 不修改 useSSE hook
- ChatStream.tsx 作为"回看模式"复用，不重写
- ScorePanel / DataPoolPanel 作为回看模式 tab 复用
- Phase 1 用 CSS/HTML 实现角色头像，不依赖 PixiJS

## 架构

```
Discussion.tsx (模式切换容器)
  ├── mode: "jrpg"                          ← 默认模式
  │   └── JRPGDialogue.tsx                  ← 新组件
  │       ├── CharacterPortrait             ← 角色立绘区域
  │       ├── TypewriterText                ← 打字机文字区域
  │       ├── ThinkingBubble                ← 思考气泡
  │       └── ScoreNotification             ← 评分浮层
  │
  └── mode: "review"                        ← 切换后
      ├── ChatStream.tsx                    ← 现有组件，不动
      └── TabBar [对话] [数据池] [评分]
          ├── DataPoolPanel.tsx             ← 现有组件
          └── ScorePanel.tsx                ← 现有组件
```

### 数据流

```
SSE events (useSSE hook)
     │
     ├──► Discussion.tsx (状态: mode, status, currentRound)
     │
     ├──► JRPGDialogue.tsx
     │    ├── agent_message_start  → 切换角色立绘，开始打字
     │    ├── agent_message_chunk  → 缓冲，打字机消费
     │    ├── agent_message_complete → 快进剩余文字
     │    ├── agent_thinking       → 显示思考气泡
     │    ├── score_update         → 浮层通知
     │    ├── moderator_guidance   → 切换到主持人立绘
     │    ├── discussion_start     → 主持人开场
     │    ├── round_start          → 更新轮次
     │    ├── data_fetch_start     → 切换研究员立绘
     │    ├── data_fetch_complete  → 数据通知
     │    └── error                → 错误提示
     │
     └──► ChatStream.tsx (review mode, 现有逻辑不动)
```

### 布局

```
JRPG 模式 (默认):                       回看模式:
┌─────────────────────────────┐         ┌─────────────────────────────┐
│                             │         │                             │
│   (背景变暗/模糊)           │         │  ChatStream (完整历史)       │
│                             │         │  辩手A: 我认为... [1][2]    │
│   ┌──────┐                  │         │  评委: 85/90                │
│   │ 角色 │  角色名           │         │  辩手B: 我不同意...          │
│   │ 立绘 │                  │         │                             │
│   │      │  打字机文字...    │         │  [对话] [数据池] [评分]     │
│   │      │  [1][2] 引用     │         │  (DataPoolPanel / ScorePanel│
│   │      │                  │         │   作为 tab 内容)             │
│   │      │  ▼ (思考气泡)    │         │                             │
│   └──────┘                  │         │                             │
│                             │         │                             │
│  [⚔ 对话]  [📜 回看]       │         │  [⚔ 对话]  [📜 回看]       │
└─────────────────────────────┘         └─────────────────────────────┘
```

### 移动端布局

JRPG 模式在手机上效果更好（全屏文字，无分屏挤压）：
- 角色立绘缩小为圆形头像（左上角）
- 文字区域占满屏幕宽度
- 底部切换按钮保持

## 角色配置

### 角色映射

```
角色 ID              → 角色类型     → 颜色标识      → 默认头像
moderator            → 主持人       → amber         → 🎓 或自定义图
data_clerk           → 数据研究员   → emerald       → 🔍 或自定义图
scorer               → 评委         → violet        → 📊 或自定义图
debater_0..5         → 辩手         → 按颜色轮换    → 🗣️ 或自定义图
```

### 颜色标识 (复用现有 POSITION_COLORS)

```typescript
const AGENT_COLORS = {
  moderator: "text-amber-400",
  data_clerk: "text-emerald-400",
  scorer: "text-violet-400",
  debater: [
    "text-emerald-400",
    "text-amber-400",
    "text-red-400",
    "text-violet-400",
    "text-pink-400",
    "text-cyan-400",
  ],
};
```

### Phase 1 头像方案

**不引入任何图片资源。** 用 CSS 绘制角色标识：

```
┌──────────────────┐
│  ┌────┐          │
│  │ 🎓 │  主持人   │    ← 圆形背景 + emoji + 角色名
│  │    │          │       每个角色类型有固定颜色
│  └────┘          │       辩手按 position_id 分配颜色
│                  │
│  角色发言文字     │
│  打字机效果...    │
└──────────────────┘
```

Phase 2 可替换为 AI 生成的像素风头像图片。

## 核心组件设计

### 1. JRPGDialogue.tsx

**职责**: 管理 JRPG 对话模式的渲染和状态

```typescript
interface JRPGDialogueProps {
  events: SSEEvent[];
}

// 内部状态
interface JRPGState {
  currentSpeaker: {
    agentId: string;
    agentName: string;
    role: "moderator" | "data_clerk" | "scorer" | "debater";
    colorIndex: number;
  } | null;
  displayText: string;         // 已显示的文字（打字机输出）
  pendingChunks: string[];     // 缓冲的 SSE chunks
  isTyping: boolean;           // 打字机正在输出
  isComplete: boolean;         // 当前发言完成
  thinking: string | null;     // 思考内容
  showThinking: boolean;       // 思考面板展开
  scoreNotifications: ScoreNotification[];
}
```

**关键逻辑**:

```
SSE 事件处理:
  agent_message_start:
    1. 如果当前有未完成发言 → 快进打字机到末尾 (100ms 加速)
    2. 切换角色立绘 (淡入动画 300ms)
    3. 清空 displayText, pendingChunks
    4. 设置 currentSpeaker

  agent_message_chunk:
    1. 追加到 pendingChunks
    2. 如果打字机空闲 → 开始消费

  agent_message_complete:
    1. 将完整 content 设为目标
    2. 快进打字机到末尾 (50ms/字加速)
    3. 标记 isComplete = true

  agent_thinking:
    1. 设置 thinking 内容
    2. 自动展开 showThinking = true

  score_update:
    1. 添加浮层通知 (3秒后消失)

  moderator_guidance:
    1. 同 agent_message_start 但角色切换到主持人

  discussion_start:
    1. 切换到主持人立绘
    2. 显示 opening 文字
```

### 2. 打字机引擎

**不使用额外库**。用 `useState` + `useEffect` + `requestAnimationFrame` 或 `setInterval` 实现。

```typescript
// 打字机参数
const TYPE_SPEED = 40;          // 每字间隔 ms (中文约 40ms/字)
const FAST_FORWARD_SPEED = 15;  // 快进时 15ms/字
const BUFFER_DRAIN_SPEED = 30;  // 缓冲消费间隔

// 打字机逻辑 (在 JRPGDialogue 内部)
function useTypewriter(pendingChunks, onComplete) {
  const [displayed, setDisplayed] = useState("");
  const targetRef = useRef("");
  const cursorRef = useRef(0);

  // 新 chunk 来了 → 更新 target
  useEffect(() => {
    const newTarget = pendingChunks.join("");
    targetRef.current = newTarget;
  }, [pendingChunks]);

  // 定时器消费 target → displayed
  useEffect(() => {
    const timer = setInterval(() => {
      setDisplayed(prev => {
        const target = targetRef.current;
        if (prev.length >= target.length) {
          clearInterval(timer);
          onComplete?.();
          return prev;
        }
        return target.slice(0, prev.length + 1);
      });
    }, TYPE_SPEED);
    return () => clearInterval(timer);
  }, [pendingChunks]);

  return displayed;
}
```

**快进逻辑**:
- 角色切换时 (新 `agent_message_start`)：直接 `setDisplayed(targetRef.current)`
- `agent_message_complete` 时：加速到 15ms/字 直到追上

**性能考虑**:
- 打字机每 40ms 更新一次 state → React re-render。中文 500 字发言 = 500 次 render × 40ms = 20 秒。这是可接受的（每帧只改一个字符，DOM diff 很小）
- 如果发现性能问题，可改用 `requestAnimationFrame` + 批量输出（每帧输出 N 个字符）

### 3. 引用 [N] 渲染

复用现有 `CitationText` 子组件逻辑（从 ChatStream.tsx 提取或直接 import）。

```
JRPG 文字区域:
  "我认为这个政策有问题 [1]，因为数据显示... [2]"
                   ↑ 蓝色圆形 badge，可点击弹出 tooltip
```

需要：
- 从 events 中构建 `poolMap: Map<number, DataPoolEntry>`（复用 ChatStream 的逻辑）
- 打字机输出文字时实时解析 [N] 标记

**注意**: 打字机逐字输出时，`[1]` 中的 `[` 和 `1` 和 `]` 不是同时出现的。解析逻辑需要：
- 方案 A：打字机输出纯文本，完成后再解析引用（简单但延迟）
- 方案 B：打字机输出时按 token 输出（一个 [N] 引用是一个 token，一次输出），不会拆分（更复杂但即时）

**推荐方案 A**（简单）。打字机完成后（`isComplete = true`），切换为 CitationText 解析渲染。打字进行中只显示纯文本。

### 4. 思考气泡

```
  ┌──────┐
  │ 🎓   │  主持人 · 开场白
  │      │
  │      │  各位辩手，今天的议题是...
  └──────┘

  ┌─ 💭 思考过程 ────────────────┐
  │ 分析当前局势...              │
  │ 辩手A的论点有漏洞...         │
  └──────────────────────────────┘
```

- 默认折叠（点击展开）
- `agent_thinking` 到达时自动展开 1 秒后收起
- 琥珀色边框（复用 ChatStream 的思考面板样式）

### 5. 评分浮层通知

```
  ┌──────────────────────────────┐
  │                    ┌───────┐ │
  │  (角色立绘+文字)    │ ⭐ 85 │ │  ← 浮层，3秒后淡出
  │                    └───────┘ │
  │                              │
  └──────────────────────────────┘
```

- `score_update` 事件到达时显示
- 固定在右上角
- 显示所有辩手的评分
- 3 秒后自动消失（带淡出动画）
- 不打断当前打字机

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `src/components/JRPGDialogue.tsx` | JRPG 对话组件 (~300 行) |
| **大改** | `src/pages/Discussion.tsx` | 模式切换 + 布局重构 |
| **小改** | `src/components/ChatStream.tsx` | 导出 CitationText 子组件（供 JRPGDialogue 复用） |
| **小改** | `src/main.tsx` | 删除 `/demo` 路由（可选，清理无用代码） |
| 不动 | `src/hooks/useSSE.ts` | SSE 逻辑不变 |
| 不动 | `src/components/ScorePanel.tsx` | 回看模式下复用 |
| 不动 | `src/components/DataPoolPanel.tsx` | 回看模式下复用 |
| 不动 | `src/lib/api.ts` | API 不变 |
| 不动 | 后端所有文件 | 不碰 |

**可选清理**: `src/game/` 目录是旧 2.5D 原型代码，可以删除或保留（不影响新功能）。

## Implementation Phases

### Phase 1: JRPG 对话模式核心

**目标**: Discussion 页面默认显示 JRPG 对话模式，完整走完辩论流程

1. **`src/components/JRPGDialogue.tsx`** — 新建 JRPG 对话组件
   - 角色头像区域 (CSS 圆形 + emoji)
   - 角色名显示
   - 打字机文字区域
   - 思考气泡 (折叠/展开)
   - 引用 [N] 渲染 (完成后切换为 CitationText)
   - 评分浮层通知
   - 全屏暗色背景 + 聚焦效果
   - 角色切换淡入淡出动画 (CSS transition)

2. **`src/components/ChatStream.tsx`** — 导出 CitationText
   - 将 `CitationText` 和 `DataPoolEntry` 类型 export
   - 不改内部逻辑

3. **`src/pages/Discussion.tsx`** — 重构
   - 新增 `mode` state: `"jrpg" | "review"`
   - JRPG 模式：全屏 `<JRPGDialogue />`
   - Review 模式：现有布局 (`ChatStream` + tab 切换 `DataPoolPanel` / `ScorePanel`)
   - 底部模式切换按钮: `[⚔ 对话] [📜 回看]`
   - 保留现有 header (状态指示、连接状态)
   - review 模式下 tab 切换：`[对话] [数据池] [评分]`

4. **打字机引擎** (JRPGDialogue 内部)
   - `useTypewriter` hook
   - SSE chunk 缓冲 → 逐字输出
   - 快进逻辑 (角色切换 / complete 时)
   - TYPE_SPEED = 40ms, FAST_FORWARD = 15ms

**Phase 1 完成标准**:
- 打开 Discussion 页面默认看到 JRPG 模式
- 角色发言时：背景变暗 + 角色头像 + 名字 + 打字机文字
- 角色切换有淡入淡出
- 思考气泡显示/折叠
- 评分浮层通知
- 点击"回看"切换到 ChatStream 视图，点击"对话"切回 JRPG
- 所有现有 SSE 事件正确处理
- [N] 引用在发言完成后可点击

---

### Phase 2: 角色头像 + 视觉打磨

**目标**: 用图片替换 emoji 头像，增强视觉体验

5. **角色头像资源**
   - 8 张角色图 (主持人/研究员/评委/6辩手)
   - 像素风或简约插画风格
   - 尺寸: 256x256 px (显示时缩放)
   - 放在 `frontend/public/assets/portraits/`
   - Phase 2 之前可先用 AI 生成

6. **JRPGDialogue 视觉增强**
   - 角色立绘替换 emoji 头像
   - 背景变暗时加模糊效果 (`backdrop-blur`)
   - 发言完成时文字颜色变淡 (ghost 状态)
   - 打字机光标闪烁动画

7. **音效 (可选)**
   - 角色切换音 (whoosh)
   - 打字机按键音 (tick)
   - 评分音 (ding)
   - 用 Web Audio API 程序化生成，零依赖

**Phase 2 完成标准**: 角色有真实头像，视觉体验接近游戏感。

---

### Phase 3: 清理 + 优化

8. **删除旧代码**
   - `src/game/` 目录（旧 2.5D 原型）
   - `src/main.tsx` 中的 `/demo` 路由
   - `frontend/public/assets/` 中的 PixiJS 素材

9. **移动端优化**
   - JRPG 模式：头像缩小为圆形，文字全宽
   - 回看模式：现有 ChatStream 已做过移动端适配
   - 底部切换按钮固定在 viewport 底部

10. **性能优化**
    - 打字机 state 更新频率检查
    - 大量历史消息时 review 模式滚动性能
    - 角色切换动画 GPU 加速 (`will-change: transform`)

## Key Technical Decisions

### 1. HTML/CSS vs PixiJS

**Phase 1 用纯 HTML/CSS/React**。不用 PixiJS。

原因:
- JRPG 对话本质是文本渲染 + CSS 动画，不需要 WebGL
- HTML 文本渲染天然支持滚动、选中、引用点击
- PixiJS 的 canvas 文本渲染中文字体支持差
- 零新增依赖
- 如果 Phase 2 需要粒子效果等，再考虑局部引入

### 2. 打字机 vs 直接显示

**打字机效果**。核心体验。

- 每字 40ms，中文 500 字发言 ≈ 20 秒完整输出
- 如果用户觉得慢：可以在打字区域添加"跳过"按钮
- 快进模式 (新发言者到达时)：15ms/字

### 3. 模式切换不丢状态

两个模式共享 `events` 数组（来自 useSSE）。切换模式时：
- JRPG → Review：Review 模式的 ChatStream 显示所有历史消息
- Review → JRPG：JRPG 模式恢复到最后一个发言者的状态

实现：ChatStream 不销毁，用 CSS `display: none` / `hidden` 隐藏。JRPGDialogue 同理。这样切换时不会丢失滚动位置和打字状态。

### 4. 引用渲染时机

打字过程中显示纯文本，完成（`isComplete`）后切换为 CitationText 组件渲染。

原因：`[1]` 在打字机逐字输出时会被拆成 `[` → `1` → `]`，视觉上不好看。完成后一次性解析渲染更干净。

## SSE Event → JRPG Action 映射

| SSE Event | JRPG Action | 视觉效果 |
|-----------|-------------|---------|
| `discussion_start` | 主持人立绘 + opening 文字 | 背景变暗，主持人淡入 |
| `round_start` | 轮次更新 | 轮次指示器闪烁 |
| `data_fetch_start` | 研究员立绘 | "正在搜索数据..." |
| `data_fetch_complete` | 研究员文字 + 数据通知 | "找到 N 条相关数据" |
| `user_data_added` | 数据通知浮层 | "用户贡献了数据" |
| `agent_thinking` | 思考气泡展开 | 琥珀色气泡，1秒后自动收起 |
| `agent_message_start` | 切换角色立绘 + 清空文字 | 淡入新角色 (300ms) |
| `agent_message_chunk` | 打字机输出 | 文字逐字出现 |
| `agent_message_complete` | 快进打字机 + 标记完成 | 文字颜色变淡 |
| `score_update` | 评分浮层 | 右上角显示评分，3秒后消失 |
| `moderator_guidance` | 主持人立绘 + 文字 | 主持人插话 |
| `round_complete` | 轮次结束 | 短暂全屏闪烁 |
| `discussion_end` | 讨论结束 | 切换到"查看裁决"按钮 |
| `error` | 错误提示 | 红色边框 + 错误文字 |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| 打字机 40ms setState 性能 | 可能掉帧 | 改用 requestAnimationFrame + 批量输出 |
| 角色切换时 chunk 竞态 | 文字显示错乱 | 快进逻辑 + setState 队列化 |
| 移动端长文字溢出 | 布局错乱 | 文字区域 max-height + 滚动 |
| ChatStream hidden 不销毁内存 | 内存增长 | 超过 500 条消息时虚拟滚动 |
| 回看模式滚动位置丢失 | 切换后回到顶部 | 保存/恢复 scrollTop |

## Verification Checklist

- [ ] Discussion 页面默认显示 JRPG 模式
- [ ] 角色发言：立绘 + 打字机文字 + 角色名
- [ ] 角色切换有过渡动画
- [ ] 思考气泡显示/折叠
- [ ] 评分浮层通知 (3秒消失)
- [ ] [N] 引用可点击弹出 tooltip
- [ ] 数据研究员搜索事件正确显示
- [ ] 切换到回看模式 → ChatStream 显示完整历史
- [ ] 切换回 JRPG 模式 → 恢复最后发言者状态
- [ ] 模式切换不丢消息
- [ ] 辩论结束后显示"查看裁决"按钮
- [ ] 错误状态正确显示
- [ ] 现有功能 (Home → Positions → Discussion → Minutes) 不受影响
- [ ] 移动端 JRPG 模式布局正常
- [ ] TypeScript 编译通过
