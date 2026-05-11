import { useEffect, useMemo, useRef, useState } from "react";
import type { SSEEvent } from "../hooks/useSSE";

const POSITION_COLORS = [
  "text-emerald-400",
  "text-amber-400",
  "text-red-400",
  "text-violet-400",
  "text-pink-400",
  "text-cyan-400",
];

interface AgentMessage {
  id: string;
  agentName: string;
  positionId: string;
  content: string;
  round: number;
  complete: boolean;
  isDataFetch?: boolean;
  resultCount?: number;
  dataNeed?: string;
  searchQueries?: string[];
  thinking?: string;
  thinkingExpanded?: boolean;
}

interface DataPoolEntry {
  title: string;
  snippet: string;
  url: string;
  keyFacts?: string[];
}

interface ChatStreamProps {
  events: SSEEvent[];
}

/** Parse [N] citations from text and render as badges with tooltips */
function CitationText({ text, poolMap }: { text: string; poolMap: Map<number, DataPoolEntry> }) {
  const [activeTooltip, setActiveTooltip] = useState<number | null>(null);

  const parts = useMemo(() => {
    const result: Array<{ type: "text" | "cite"; value: string; num: number }> = [];
    // Match [N] patterns where N is a number
    const regex = /\[(\d+)\]/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        result.push({ type: "text", value: text.slice(lastIndex, match.index), num: 0 });
      }
      result.push({ type: "cite", value: match[0], num: parseInt(match[1], 10) });
      lastIndex = regex.lastIndex;
    }
    if (lastIndex < text.length) {
      result.push({ type: "text", value: text.slice(lastIndex), num: 0 });
    }
    return result;
  }, [text]);

  if (parts.length === 0 || (parts.length === 1 && parts[0].type === "text")) {
    return <>{text}</>;
  }

  return (
    <>
      {parts.map((part, i) => {
        if (part.type === "text") return <span key={i}>{part.value}</span>;

        const entry = poolMap.get(part.num);
        return (
          <span key={i} className="relative inline-block">
            <button
              className="inline-flex items-center justify-center
                w-4 h-4 text-[10px] font-bold leading-none
                bg-blue-600/80 hover:bg-blue-500 text-white
                rounded-full align-super ml-0.5 mr-0.5
                cursor-pointer transition-colors"
              onClick={(e) => {
                e.stopPropagation();
                setActiveTooltip(activeTooltip === part.num ? null : part.num);
              }}
              title={entry ? `${entry.title}: ${entry.snippet.slice(0, 100)}` : `数据池 [${part.num}]`}
            >
              {part.num}
            </button>
            {activeTooltip === part.num && entry && (
              <div
                className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2
                  w-64 p-3 rounded-lg bg-neutral-800 border border-neutral-700 shadow-xl
                  text-left"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="text-xs font-semibold text-blue-400 mb-1">{entry.title}</div>
                {entry.keyFacts && entry.keyFacts.length > 0 ? (
                  <ul className="text-xs text-neutral-300 leading-relaxed mb-1.5 space-y-0.5">
                    {entry.keyFacts.slice(0, 5).map((fact, fi) => (
                      <li key={fi} className="flex gap-1">
                        <span className="text-amber-400 shrink-0">•</span>
                        <span>{fact.length > 100 ? fact.slice(0, 100) + "..." : fact}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-neutral-300 leading-relaxed mb-1.5">
                    {entry.snippet.slice(0, 200)}{entry.snippet.length > 200 ? "..." : ""}
                  </p>
                )}
                {entry.url && (
                  <a
                    href={entry.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-cyan-400 hover:text-cyan-300 truncate block max-w-full"
                  >
                    {entry.url}
                  </a>
                )}
                <button
                  className="absolute top-1 right-1.5 text-neutral-500 hover:text-neutral-300 text-xs"
                  onClick={() => setActiveTooltip(null)}
                >
                  x
                </button>
              </div>
            )}
          </span>
        );
      })}
    </>
  );
}

export function ChatStream({ events }: ChatStreamProps) {
  const [messages, setMessages] = useState<Map<string, AgentMessage>>(new Map());
  const [displayOrder, setDisplayOrder] = useState<string[]>([]);
  const [dataPool, setDataPool] = useState<Map<number, DataPoolEntry>>(new Map());
  const scrollRef = useRef<HTMLDivElement>(null);
  const poolProcessedRef = useRef(0);
  const msgProcessedRef = useRef(0);
  // Pending thinking: keyed by "agentId_round", stores thinking text
  // until agent_message_start arrives and we can attach it to the real message
  const pendingThinking = useRef<Map<string, { thinking: string; agentName: string }>>(new Map());

  // Build data pool map from SSE events
  useEffect(() => {
    const newEvents = events.slice(poolProcessedRef.current);
    if (newEvents.length === 0) return;

    let poolChanged = false;
    for (const event of newEvents) {
      if (event.type === "data_fetch_complete") {
        const results = (event.results as Array<{ title: string; snippet: string; url: string }>) ?? [];
        if (results.length > 0) {
          poolChanged = true;
        }
      }
      if (event.type === "user_data_added") {
        poolChanged = true;
      }
    }

    if (poolChanged) {
      // Rebuild full pool map from all events
      const newPool = new Map<number, DataPoolEntry>();
      let idx = 1;
      for (const event of events) {
        if (event.type === "data_fetch_complete") {
          const results = (event.results as Array<{ title: string; snippet: string; url: string; key_facts?: string }>) ?? [];
          for (const r of results) {
            let keyFacts: string[] | undefined;
            if (r.key_facts) {
              try {
                const parsed = JSON.parse(r.key_facts);
                keyFacts = parsed.key_facts;
              } catch { /* ignore */ }
            }
            newPool.set(idx++, { title: r.title, snippet: r.snippet, url: r.url, keyFacts });
          }
        }
        if (event.type === "user_data_added") {
          const data = event.data as { title?: string; snippet?: string; content?: string; url?: string };
          if (data) {
            newPool.set(idx++, {
              title: data.title || "",
              snippet: data.snippet || data.content || "",
              url: data.url || "",
            });
          }
        }
      }
      setDataPool(newPool);
    }

    poolProcessedRef.current = events.length;
  }, [events]);

  // Process message events
  useEffect(() => {
    const newEvents = events.slice(msgProcessedRef.current);
    if (newEvents.length === 0) return;

    for (const event of newEvents) {
      if (event.type === "discussion_start") {
        const id = "opening";
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: "主持人",
            positionId: "moderator",
            content: event.opening as string,
            round: 0,
            complete: true,
          });
          return next;
        });
        setDisplayOrder((prev) => [...prev, id]);
      }

      if (event.type === "agent_message_start") {
        const id = event.message_id as string;
        const agentId = event.agent as string;
        const round = event.round as number;
        const thinkKey = `${agentId}_${round}`;
        const pending = pendingThinking.current.get(thinkKey);
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: event.agent_name as string,
            positionId: agentId,
            content: "",
            round,
            complete: false,
            thinking: pending?.thinking,
            thinkingExpanded: !!pending,
          });
          return next;
        });
        if (pending) {
          pendingThinking.current.delete(thinkKey);
        }
        setDisplayOrder((prev) => prev.includes(id) ? prev : [...prev, id]);
      }

      if (event.type === "agent_message_chunk") {
        const id = event.message_id as string;
        setMessages((prev) => {
          const next = new Map(prev);
          const msg = prev.get(id);
          if (msg) {
            next.set(id, { ...msg, content: msg.content + (event.chunk as string) });
          }
          return next;
        });
      }

      if (event.type === "agent_message_complete") {
        const id = event.message_id as string;
        setMessages((prev) => {
          const next = new Map(prev);
          const msg = prev.get(id);
          if (msg) {
            next.set(id, {
              ...msg,
              content: event.content as string,
              complete: true,
            });
          }
          return next;
        });
      }

      if (event.type === "moderator_guidance") {
        const id = `guidance-${event.round}`;
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: "主持人",
            positionId: "moderator",
            content: event.content as string,
            round: event.round as number,
            complete: true,
          });
          return next;
        });
        setDisplayOrder((prev) => prev.includes(id) ? prev : [...prev, id]);
      }

      if (event.type === "data_fetch_start") {
        const id = event.message_id as string;
        const dataNeed = (event.data_need as string) ?? "";
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: `数据研究员 → ${(event.agent_name as string) ?? ""}`,
            positionId: event.agent as string,
            content: dataNeed.length > 0
              ? `正在搜索：${dataNeed}`
              : "正在搜索相关数据...",
            round: event.round as number,
            complete: false,
            isDataFetch: true,
            dataNeed,
          });
          return next;
        });
        setDisplayOrder((prev) => prev.includes(id) ? prev : [...prev, id]);
      }

      if (event.type === "data_fetch_complete") {
        const id = event.message_id as string;
        const results = (event.results as Array<{ title: string; snippet: string; url: string }>) ?? [];
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: `数据研究员 → ${(event.agent_name as string) ?? ""}`,
            positionId: event.agent as string,
            content: `找到 ${results.length} 条相关数据，查看数据池`,
            round: event.round as number,
            complete: true,
            isDataFetch: true,
            resultCount: results.length,
          });
          return next;
        });
      }

      // Handle search queries during debate — update existing data_fetch message
      if (event.type === "search_queries") {
        const round = event.round as number;
        const queries = (event.queries as string[]) ?? [];
        setMessages((prev) => {
          const next = new Map(prev);
          // Find the data_fetch message for this agent/round
          for (const [id, msg] of next) {
            if (msg.isDataFetch && msg.round === round && !msg.complete) {
              next.set(id, {
                ...msg,
                content: queries.length > 0
                  ? `搜索关键词：${queries.join("、")}`
                  : msg.content,
                searchQueries: queries,
              });
              break;
            }
          }
          return next;
        });
      }

      // Handle search results during debate — update existing data_fetch message
      if (event.type === "search_results") {
        const round = event.round as number;
        const results = (event.results as Array<{ title: string; snippet: string; url: string }>) ?? [];
        setMessages((prev) => {
          const next = new Map(prev);
          for (const [id, msg] of next) {
            if (msg.isDataFetch && msg.round === round && !msg.complete) {
              const queryLabel = msg.searchQueries?.length
                ? `关键词：${msg.searchQueries.join("、")}`
                : "";
              const resultItems = results
                .slice(0, 3)
                .map((r) => r.title)
                .join("、");
              next.set(id, {
                ...msg,
                content: queryLabel
                  ? `${queryLabel}\n找到 ${results.length} 条结果：${resultItems}`
                  : `找到 ${results.length} 条结果：${resultItems}`,
              });
              break;
            }
          }
          return next;
        });
      }

      // Handle agent thinking events — store in ref until agent_message_start arrives
      if (event.type === "agent_thinking") {
        const agentId = event.agent as string;
        const round = event.round as number;
        const thinkKey = `${agentId}_${round}`;
        const thinking = event.thinking as string;
        const agentName = (event.agent_name as string) ?? "";

        // Check if the message already exists (agent_message_start arrived before thinking)
        let attached = false;
        setMessages((prev) => {
          const next = new Map(prev);
          for (const [id, msg] of next) {
            if (msg.positionId === agentId && msg.round === round && !msg.complete && !msg.isDataFetch) {
              next.set(id, { ...msg, thinking, thinkingExpanded: true });
              attached = true;
              break;
            }
          }
          return next;
        });
        // Otherwise, store for later attachment when agent_message_start arrives
        if (!attached) {
          pendingThinking.current.set(thinkKey, { thinking, agentName });
        }
      }
    }

    msgProcessedRef.current = events.length;
  }, [events]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  function getPositionColorIndex(positionId: string): number {
    const index = parseInt(positionId.replace(/\D/g, ""), 10);
    return isNaN(index) ? 0 : index % POSITION_COLORS.length;
  }

  return (
    <div
      ref={scrollRef}
      className="flex flex-col gap-3 overflow-y-auto h-full pr-2"
    >
      {displayOrder.map((id) => {
        const msg = messages.get(id);
        if (!msg) return null;

        const isModerator = msg.positionId === "moderator";
        const colorClass = isModerator
          ? "text-blue-400"
          : POSITION_COLORS[getPositionColorIndex(msg.positionId)];

        return (
          <div
            key={id}
            className={`p-4 rounded-xl ${
              isModerator
                ? "bg-blue-950/30 border border-blue-900/50"
                : msg.isDataFetch
                ? "bg-cyan-950/20 border border-cyan-900/40"
                : "bg-neutral-900"
            }`}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className={`font-semibold text-sm ${
                msg.isDataFetch ? "text-cyan-400" : colorClass
              }`}>
                {msg.agentName}
              </span>
              <span className="text-xs text-neutral-600">
                第 {msg.round} 轮
              </span>
              {!msg.complete && (
                <span className="inline-block w-1.5 h-4 bg-current animate-pulse" />
              )}
            </div>
            {msg.isDataFetch ? (
              <div className="text-xs text-neutral-400 whitespace-pre-wrap">
                {msg.complete ? (
                  `${msg.resultCount ?? 0} 条数据已添加到数据池`
                ) : (
                  <>
                    {msg.content}
                    {!msg.complete && (
                      <span className="inline-block w-1.5 h-3 bg-cyan-400 animate-pulse ml-1 align-middle" />
                    )}
                  </>
                )}
              </div>
            ) : (
              <>
                {msg.thinking && (
                  <div
                    className={`mb-2 rounded-lg border transition-all duration-300 ${
                      msg.thinkingExpanded
                        ? "border-amber-800/60 bg-amber-950/20 p-3"
                        : "border-neutral-800 bg-neutral-900/50 p-2 cursor-pointer hover:bg-neutral-800/50"
                    }`}
                    onClick={() => {
                      if (!msg.thinkingExpanded) {
                        setMessages((prev) => {
                          const next = new Map(prev);
                          next.set(msg.id, { ...msg, thinkingExpanded: true });
                          return next;
                        });
                      }
                    }}
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className="text-amber-400 text-xs font-medium">
                        {msg.thinkingExpanded ? "思考过程" : "思考过程 ▸"}
                      </span>
                      {!msg.complete && (
                        <span className="inline-block w-1 h-1 bg-amber-400 rounded-full animate-pulse" />
                      )}
                    </div>
                    {msg.thinkingExpanded && (
                      <p className="text-xs text-neutral-400 leading-relaxed whitespace-pre-wrap">
                        {msg.thinking}
                      </p>
                    )}
                  </div>
                )}
                <p className="text-neutral-300 text-sm leading-relaxed whitespace-pre-wrap">
                  <CitationText text={msg.content} poolMap={dataPool} />
                </p>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
