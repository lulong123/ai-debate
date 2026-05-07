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
}

interface DataPoolEntry {
  title: string;
  snippet: string;
  url: string;
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
                <p className="text-xs text-neutral-300 leading-relaxed mb-1.5">
                  {entry.snippet.slice(0, 200)}{entry.snippet.length > 200 ? "..." : ""}
                </p>
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
          const results = (event.results as Array<{ title: string; snippet: string; url: string }>) ?? [];
          for (const r of results) {
            newPool.set(idx++, { title: r.title, snippet: r.snippet, url: r.url });
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
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: event.agent_name as string,
            positionId: event.agent as string,
            content: "",
            round: event.round as number,
            complete: false,
          });
          return next;
        });
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
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: `数据研究员 → ${(event.agent_name as string) ?? ""}`,
            positionId: event.agent as string,
            content: "",
            round: event.round as number,
            complete: false,
            isDataFetch: true,
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
            {msg.isDataFetch && msg.complete ? (
              <div className="text-xs text-neutral-400">
                {msg.resultCount ?? 0} 条数据已添加到数据池
              </div>
            ) : (
              <p className="text-neutral-300 text-sm leading-relaxed whitespace-pre-wrap">
                <CitationText text={msg.content} poolMap={dataPool} />
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
