import { useEffect, useMemo, useRef, useState } from "react";
import type { SSEEvent } from "../hooks/useSSE";
import { useTypewriter } from "../hooks/useTypewriter";
import { CitationText } from "./CitationText";
import type { DataPoolEntry } from "./CitationText";

/* ─── 角色配置 ─── */

interface AgentInfo {
  agentId: string;
  agentName: string;
  role: "moderator" | "data_clerk" | "scorer" | "debater";
  colorClass: string;
  bgColorClass: string;
  emoji: string;
}

const AGENT_COLORS: Record<string, string> = {
  moderator: "text-amber-400",
  data_clerk: "text-emerald-400",
  scorer: "text-violet-400",
};

const AGENT_BG_COLORS: Record<string, string> = {
  moderator: "bg-amber-500/20 border-amber-500/40",
  data_clerk: "bg-emerald-500/20 border-emerald-500/40",
  scorer: "bg-violet-500/20 border-violet-500/40",
};

const DEBATER_COLORS = [
  { text: "text-emerald-400", bg: "bg-emerald-500/20 border-emerald-500/40" },
  { text: "text-amber-400", bg: "bg-amber-500/20 border-amber-500/40" },
  { text: "text-red-400", bg: "bg-red-500/20 border-red-500/40" },
  { text: "text-violet-400", bg: "bg-violet-500/20 border-violet-500/40" },
  { text: "text-pink-400", bg: "bg-pink-500/20 border-pink-500/40" },
  { text: "text-cyan-400", bg: "bg-cyan-500/20 border-cyan-500/40" },
];

const ROLE_EMOJIS: Record<string, string> = {
  moderator: "🎓",
  data_clerk: "🔍",
  scorer: "📊",
};

function parseAgent(event: SSEEvent): AgentInfo {
  const agentId = (event.agent_id as string) ?? "";
  const agentName = (event.agent_name as string) ?? agentId;

  let role: AgentInfo["role"] = "debater";
  if (agentId === "moderator") role = "moderator";
  else if (agentId === "data_clerk") role = "data_clerk";
  else if (agentId === "scorer") role = "scorer";

  const colorIndex = parseInt((event.position_id as string)?.replace("position_", "") ?? "0", 10) || 0;

  if (role !== "debater") {
    return {
      agentId, agentName, role,
      colorClass: AGENT_COLORS[role] ?? "text-neutral-400",
      bgColorClass: AGENT_BG_COLORS[role] ?? "bg-neutral-500/20 border-neutral-500/40",
      emoji: ROLE_EMOJIS[role] ?? "🗣️",
    };
  }

  const dc = DEBATER_COLORS[colorIndex % DEBATER_COLORS.length];
  return {
    agentId, agentName, role,
    colorClass: dc.text,
    bgColorClass: dc.bg,
    emoji: "🗣️",
  };
}

/* ─── 评分通知 ─── */

interface ScoreNotif {
  id: number;
  agentName: string;
  scores: Record<string, number>;
  timestamp: number;
}

/* ─── 主组件 ─── */

interface JRPGDialogueProps {
  events: SSEEvent[];
  connected: boolean;
}

export default function JRPGDialogue({ events, connected }: JRPGDialogueProps) {
  const [currentAgent, setCurrentAgent] = useState<AgentInfo | null>(null);
  const [messageBuffer, setMessageBuffer] = useState("");
  const [isComplete, setIsComplete] = useState(false);
  const [thinking, setThinking] = useState<string | null>(null);
  const [showThinking, setShowThinking] = useState(false);
  const [scoreNotifs, setScoreNotifs] = useState<ScoreNotif[]>([]);
  const [currentRound, setCurrentRound] = useState(0);
  const [statusText, setStatusText] = useState("等待连接...");
  const [transitioning, setTransitioning] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const { displayed, isTyping, setTarget, fastForward } = useTypewriter();
  const scoreIdRef = useRef(0);
  const textAreaRef = useRef<HTMLDivElement>(null);
  const thinkingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Build poolMap from events
  const poolMap = useMemo(() => {
    const map = new Map<number, DataPoolEntry>();
    let idx = 1;
    for (const ev of events) {
      if (ev.type === "data_fetch_complete") {
        const items = ev.data_items as Array<{ title: string; snippet: string; url: string }> | undefined;
        if (items) {
          for (const item of items) {
            map.set(idx, { title: item.title, snippet: item.snippet, url: item.url });
            idx++;
          }
        }
      }
      if (ev.type === "user_data_added") {
        map.set(idx, {
          title: (ev.title as string) ?? "用户数据",
          snippet: (ev.content as string) ?? "",
          url: (ev.url as string) ?? "",
        });
        idx++;
      }
    }
    return map;
  }, [events]);

  // Process SSE events
  useEffect(() => {
    if (events.length === 0) return;
    const ev = events[events.length - 1];

    switch (ev.type) {
      case "discussion_start": {
        const agent: AgentInfo = {
          agentId: "moderator",
          agentName: "主持人",
          role: "moderator",
          colorClass: AGENT_COLORS.moderator,
          bgColorClass: AGENT_BG_COLORS.moderator,
          emoji: ROLE_EMOJIS.moderator,
        };
        switchAgent(agent, ev.opening as string ?? "辩论开始！");
        setStatusText("辩论进行中");
        setErrorMessage(null);
        break;
      }

      case "round_start":
        setCurrentRound((ev.round_number as number) ?? currentRound + 1);
        break;

      case "agent_message_start": {
        const agent = parseAgent(ev);
        switchAgent(agent, "");
        break;
      }

      case "agent_message_chunk": {
        const chunk = (ev.content as string) ?? "";
        setMessageBuffer((prev) => {
          const next = prev + chunk;
          setTarget(next);
          return next;
        });
        break;
      }

      case "agent_message_complete": {
        const content = (ev.content as string) ?? "";
        setMessageBuffer(content);
        // Speed up to finish quickly
        speedUpAndComplete(content);
        break;
      }

      case "agent_thinking": {
        const content = (ev.content as string) ?? "";
        setThinking(content);
        setShowThinking(true);
        // Auto-collapse after 4s
        if (thinkingTimerRef.current) clearTimeout(thinkingTimerRef.current);
        thinkingTimerRef.current = setTimeout(() => setShowThinking(false), 4000);
        break;
      }

      case "moderator_guidance": {
        const agent: AgentInfo = {
          agentId: "moderator",
          agentName: "主持人",
          role: "moderator",
          colorClass: AGENT_COLORS.moderator,
          bgColorClass: AGENT_BG_COLORS.moderator,
          emoji: ROLE_EMOJIS.moderator,
        };
        const content = (ev.content as string) ?? "";
        switchAgent(agent, content);
        break;
      }

      case "score_update": {
        const scores = (ev.scores as Record<string, number>) ?? {};
        const name = (ev.agent_name as string) ?? "辩手";
        const id = ++scoreIdRef.current;
        setScoreNotifs((prev) => [...prev, { id, agentName: name, scores, timestamp: Date.now() }]);
        // Remove after 3s
        setTimeout(() => {
          setScoreNotifs((prev) => prev.filter((n) => n.id !== id));
        }, 3000);
        break;
      }

      case "data_fetch_start": {
        const agent: AgentInfo = {
          agentId: "data_clerk",
          agentName: "数据研究员",
          role: "data_clerk",
          colorClass: AGENT_COLORS.data_clerk,
          bgColorClass: AGENT_BG_COLORS.data_clerk,
          emoji: ROLE_EMOJIS.data_clerk,
        };
        switchAgent(agent, "正在搜索相关数据...");
        break;
      }

      case "data_fetch_complete": {
        const count = (ev.data_items as unknown[])?.length ?? 0;
        setMessageBuffer(`找到了 ${count} 条相关数据。`);
        setTarget(`找到了 ${count} 条相关数据。`);
        break;
      }

      case "discussion_end":
        setStatusText("辩论结束");
        if (ev.summary) {
          setMessageBuffer(ev.summary as string);
          setTarget(ev.summary as string);
        }
        break;

      case "round_complete":
        // Brief flash effect - handled via status
        setStatusText(`第 ${currentRound} 轮结束`);
        break;

      case "error":
        setErrorMessage((ev.message as string) ?? "发生错误");
        break;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  // Auto-scroll text area
  useEffect(() => {
    if (textAreaRef.current) {
      textAreaRef.current.scrollTop = textAreaRef.current.scrollHeight;
    }
  }, [displayed]);

  // Cleanup thinking timer
  useEffect(() => {
    return () => {
      if (thinkingTimerRef.current) clearTimeout(thinkingTimerRef.current);
    };
  }, []);

  function switchAgent(agent: AgentInfo, initialText: string) {
    // Fast-forward current text
    fastForward();
    // Transition animation
    setTransitioning(true);
    setTimeout(() => {
      setCurrentAgent(agent);
      setMessageBuffer(initialText);
      setIsComplete(false);
      setTarget(initialText);
      setTransitioning(false);
    }, 150);
  }

  function speedUpAndComplete(content: string) {
    setTarget(content, () => setIsComplete(true));
  }

  const renderText = () => {
    if (isComplete && messageBuffer) {
      return <CitationText text={messageBuffer} poolMap={poolMap} />;
    }
    return <>{displayed}{isTyping && <span className="animate-pulse">▌</span>}</>;
  };

  return (
    <div className="relative w-full h-screen flex flex-col bg-neutral-950 overflow-hidden">
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-b from-neutral-900 via-neutral-950 to-black opacity-80" />

      {/* Status bar */}
      <div className="relative z-10 flex items-center justify-between px-4 py-2 border-b border-neutral-800/50">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"} animate-pulse`} />
          <span className="text-xs text-neutral-400">
            {statusText}
            {currentRound > 0 && ` · 第 ${currentRound} 轮`}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {currentRound > 0 && Array.from({ length: currentRound }, (_, i) => (
            <span key={i} className="w-1.5 h-1.5 rounded-full bg-amber-500/60" />
          ))}
        </div>
      </div>

      {/* Error banner */}
      {errorMessage && (
        <div className="relative z-10 mx-4 mt-2 px-4 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          ⚠ {errorMessage}
        </div>
      )}

      {/* Main content */}
      <div className="relative z-10 flex-1 flex flex-col items-center justify-center px-4 py-6">
        {currentAgent ? (
          <div
            className={`w-full max-w-2xl transition-opacity duration-300 ${
              transitioning ? "opacity-0" : "opacity-100"
            }`}
          >
            {/* Character portrait area */}
            <div className="flex items-start gap-4 mb-6">
              {/* Avatar */}
              <div className={`flex-shrink-0 w-16 h-16 sm:w-20 sm:h-20 rounded-full border-2 flex items-center justify-center text-3xl sm:text-4xl ${currentAgent.bgColorClass} transition-all duration-300`}>
                {currentAgent.emoji}
              </div>

              {/* Name & role badge */}
              <div className="pt-1">
                <h2 className={`text-xl sm:text-2xl font-bold ${currentAgent.colorClass}`}>
                  {currentAgent.agentName}
                </h2>
                <span className={`text-xs px-2 py-0.5 rounded-full ${currentAgent.bgColorClass} ${currentAgent.colorClass}`}>
                  {currentAgent.role === "moderator" ? "主持人" :
                   currentAgent.role === "data_clerk" ? "数据研究员" :
                   currentAgent.role === "scorer" ? "评委" : "辩手"}
                </span>
              </div>
            </div>

            {/* Text area */}
            <div
              ref={textAreaRef}
              className="min-h-[120px] max-h-[50vh] overflow-y-auto
                px-5 py-4 rounded-2xl
                bg-neutral-900/80 border border-neutral-800
                backdrop-blur-sm
                text-neutral-100 text-base sm:text-lg leading-relaxed
                scrollbar-thin scrollbar-thumb-neutral-700"
            >
              {renderText()}
            </div>

            {/* Thinking bubble */}
            {thinking && (
              <div className="mt-3">
                <button
                  onClick={() => setShowThinking(!showThinking)}
                  className="flex items-center gap-2 text-sm text-amber-400/70 hover:text-amber-400 transition-colors"
                >
                  <span>💭</span>
                  <span>{showThinking ? "收起思考" : "查看思考过程"}</span>
                  <span className={`transition-transform ${showThinking ? "rotate-180" : ""}`}>▾</span>
                </button>
                {showThinking && (
                  <div className="mt-2 px-4 py-3 rounded-xl bg-amber-500/5 border border-amber-500/20 text-sm text-neutral-400 leading-relaxed animate-in slide-in-from-top-1">
                    {thinking}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          /* Waiting state */
          <div className="text-center space-y-4 animate-pulse">
            <div className="text-6xl">⚔️</div>
            <p className="text-neutral-500 text-lg">等待辩论开始...</p>
            {!connected && (
              <p className="text-neutral-600 text-sm">正在连接服务器...</p>
            )}
          </div>
        )}
      </div>

      {/* Score notifications */}
      <div className="absolute top-16 right-4 z-20 space-y-2">
        {scoreNotifs.map((notif) => (
          <ScoreNotification key={notif.id} notif={notif} />
        ))}
      </div>
    </div>
  );
}

/* ─── 评分浮层 ─── */

function ScoreNotification({ notif }: { notif: ScoreNotif }) {
  const entries = Object.entries(notif.scores);
  return (
    <div className="px-4 py-3 rounded-xl bg-neutral-900/95 border border-amber-500/30 shadow-xl backdrop-blur-sm animate-in slide-in-from-right-2 fade-in duration-300">
      <div className="text-xs font-semibold text-amber-400 mb-1">⭐ {notif.agentName}</div>
      <div className="flex gap-3">
        {entries.map(([key, val]) => (
          <span key={key} className="text-xs text-neutral-300">
            {key}: <span className="text-amber-300 font-bold">{val}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
