import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SSEEvent } from "../hooks/useSSE";
import type { DataPoolItem, MessageResponse, PositionItem } from "../lib/api";

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
  scores?: Record<string, unknown>;
}

interface DataPoolEntry {
  title: string;
  snippet: string;
  url: string;
  keyFacts?: string[];
}

interface ChatStreamProps {
  events: SSEEvent[];
  initialMessages?: MessageResponse[] | null;
  initialPool?: DataPoolItem[] | null;
  initialPositions?: PositionItem[] | null;
}

/** Parse [N] citations from text, merge consecutive ones, render as badges with tooltips */
function CitationText({ text, poolMap, tooltipRootRef }: {
  text: string;
  poolMap: Map<number, DataPoolEntry>;
  tooltipRootRef: React.RefObject<HTMLDivElement | null>;
}) {
  const [activeTooltip, setActiveTooltip] = useState<number | null>(null);

  // Close tooltip when clicking outside
  useEffect(() => {
    if (activeTooltip === null) return;
    const handler = () => setActiveTooltip(null);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [activeTooltip]);

  // Parse text into parts, merging consecutive citations into groups
  const parts = useMemo(() => {
    type Part = { type: "text"; value: string } | { type: "cite"; nums: number[] };
    const result: Part[] = [];

    // Collect all [N] match positions
    const allMatches: Array<{ index: number; end: number; num: number }> = [];
    const re = /\[(\d+)\]/g;
    let match: RegExpExecArray | null;
    while ((match = re.exec(text)) !== null) {
      allMatches.push({ index: match.index, end: re.lastIndex, num: parseInt(match[1], 10) });
    }

    let lastIdx = 0;
    let i = 0;
    while (i < allMatches.length) {
      // Add text before this match
      if (allMatches[i].index > lastIdx) {
        result.push({ type: "text", value: text.slice(lastIdx, allMatches[i].index) });
      }
      // Collect consecutive matches (no text between them)
      const group: number[] = [allMatches[i].num];
      let end = allMatches[i].end;
      let j = i + 1;
      while (j < allMatches.length && allMatches[j].index === end) {
        group.push(allMatches[j].num);
        end = allMatches[j].end;
        j++;
      }
      result.push({ type: "cite", nums: group });
      lastIdx = end;
      i = j;
    }
    if (lastIdx < text.length) {
      result.push({ type: "text", value: text.slice(lastIdx) });
    }

    return result;
  }, [text]);

  if (parts.length === 0 || (parts.length === 1 && parts[0].type === "text")) {
    return <>{text}</>;
  }

  // Ref to the active badge button for portal positioning
  const activeBtnRef = useRef<HTMLButtonElement | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ top: number; left: number } | null>(null);

  const updateTooltipPos = useCallback(() => {
    if (!activeBtnRef.current || !tooltipRootRef.current) { setTooltipPos(null); return; }
    const btnRect = activeBtnRef.current.getBoundingClientRect();
    const rootRect = tooltipRootRef.current.getBoundingClientRect();
    const tooltipW = 288; // w-72
    // Position relative to tooltipRoot container
    let left = (btnRect.left + btnRect.width / 2) - rootRect.left - tooltipW / 2;
    left = Math.max(4, Math.min(left, rootRect.width - tooltipW - 4));
    const top = btnRect.top - rootRect.top - 8;
    setTooltipPos({ top, left });
  }, [tooltipRootRef]);

  // Update position on open
  useEffect(() => {
    if (activeTooltip === null) { setTooltipPos(null); return; }
    updateTooltipPos();
  }, [activeTooltip, updateTooltipPos]);

  // Follow scroll
  useEffect(() => {
    if (activeTooltip === null) return;
    window.addEventListener("scroll", updateTooltipPos, true);
    return () => window.removeEventListener("scroll", updateTooltipPos, true);
  }, [activeTooltip, updateTooltipPos]);

  const activePart = activeTooltip !== null ? parts[activeTooltip] : null;
  const activeNums = activePart?.type === "cite" ? activePart.nums : null;

  return (
    <>
      {parts.map((part, i) => {
        if (part.type === "text") return <span key={i}>{part.value}</span>;

        const { nums } = part;
        const label = nums.length === 1 ? String(nums[0]) : `${nums[0]}-${nums[nums.length - 1]}`;
        return (
          <span key={i} className="relative inline-block">
            <button
              ref={activeTooltip === i ? activeBtnRef : undefined}
              className="inline-flex items-center justify-center
                h-4 text-[10px] font-bold leading-none px-1
                bg-blue-600/80 hover:bg-blue-500 text-white
                rounded-full align-super ml-0.5 mr-0.5
                cursor-pointer transition-colors"
              onClick={(e) => {
                e.stopPropagation();
                setActiveTooltip(activeTooltip === i ? null : i);
              }}
            >
              {label}
            </button>
          </span>
        );
      })}
      {activeNums && tooltipPos && tooltipRootRef.current && createPortal(
        <div
          className="absolute pointer-events-auto w-72 max-h-80 overflow-y-auto p-3 rounded-lg
            bg-neutral-800 border border-neutral-700 shadow-xl text-left"
          style={{
            bottom: `calc(100% - ${tooltipPos.top}px)`,
            left: `${tooltipPos.left}px`,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {activeNums.map((num) => {
            const entry = poolMap.get(num);
            if (!entry) return null;
            return (
              <div key={num} className={num !== activeNums[0] ? "mt-2 pt-2 border-t border-neutral-700" : ""}>
                <div className="text-xs font-semibold text-blue-400 mb-1">
                  [{num}] {entry.title}
                </div>
                {entry.keyFacts && entry.keyFacts.length > 0 ? (
                  <ul className="text-xs text-neutral-300 leading-relaxed mb-1 space-y-0.5">
                    {entry.keyFacts.slice(0, 3).map((fact, fi) => (
                      <li key={fi} className="flex gap-1">
                        <span className="text-amber-400 shrink-0">•</span>
                        <span>{fact.length > 80 ? fact.slice(0, 80) + "..." : fact}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-neutral-300 leading-relaxed mb-1">
                    {entry.snippet.slice(0, 150)}{entry.snippet.length > 150 ? "..." : ""}
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
              </div>
            );
          })}
          <button
            className="absolute top-1 right-1.5 text-neutral-500 hover:text-neutral-300 text-xs"
            onClick={() => setActiveTooltip(null)}
          >
            x
          </button>
        </div>,
        tooltipRootRef.current!
      )}
    </>
  );
}

function parseKeyFacts(kf: string | null | undefined): string[] | undefined {
  if (!kf) return undefined;
  try {
    const parsed = JSON.parse(kf);
    return parsed.key_facts;
  } catch {
    return undefined;
  }
}

export function ChatStream({ events, initialMessages, initialPool, initialPositions }: ChatStreamProps) {
  const [messages, setMessages] = useState<Map<string, AgentMessage>>(new Map());
  const [displayOrder, setDisplayOrder] = useState<string[]>([]);
  const [dataPool, setDataPool] = useState<Map<number, DataPoolEntry>>(new Map());
  const scrollRef = useRef<HTMLDivElement>(null);
  const poolProcessedRef = useRef(0);
  const msgProcessedRef = useRef(0);
  const pendingThinking = useRef<Map<string, { thinking: string; agentName: string }>>(new Map());
  const historyLoaded = useRef(false);

  // Build position ID → color index map from the ordered positions array
  const positionColorMap = useMemo(() => {
    const map = new Map<string, number>();
    if (initialPositions) {
      initialPositions.forEach((pos, i) => {
        map.set(pos.id, i % POSITION_COLORS.length);
      });
    }
    return map;
  }, [initialPositions]);

  // Reconstruct data pool from API (authoritative citation numbering)
  useEffect(() => {
    if (!initialPool || initialPool.length === 0) return;
    const pool = new Map<number, DataPoolEntry>();
    for (const item of initialPool) {
      pool.set(item.citation_num, {
        title: item.title,
        snippet: item.snippet,
        url: item.url,
        keyFacts: parseKeyFacts(item.key_facts),
      });
    }
    setDataPool(pool);
    historyLoaded.current = true;
  }, [initialPool]);

  // Reconstruct messages from API history
  useEffect(() => {
    if (!initialMessages || initialMessages.length === 0) return;
    const newMessages = new Map<string, AgentMessage>();
    const newOrder: string[] = [];
    for (const m of initialMessages) {
      // Skip messages with empty content (e.g. score records)
      if (!m.content) continue;

      const id = m.id || `hist_${newOrder.length}`;
      newMessages.set(id, {
        id,
        agentName: m.agent_name || "未知",
        positionId: m.position_id || "unknown",
        content: m.content,
        round: m.round_number || 0,
        complete: true,
        scores: m.scores ?? undefined,
      });
      newOrder.push(id);
    }
    setMessages(newMessages);
    setDisplayOrder(newOrder);
  }, [initialMessages]);

  // Build data pool map from SSE events (merge with API history)
  useEffect(() => {
    const newEvents = events.slice(poolProcessedRef.current);
    if (newEvents.length === 0) return;

    let poolChanged = false;
    for (const event of newEvents) {
      if (event.type === "data_fetch_complete" || event.type === "user_data_added") {
        poolChanged = true;
        break;
      }
    }

    if (poolChanged) {
      setDataPool((prev) => {
        const newPool = new Map(prev);
        // Find max existing citation number
        let maxNum = 0;
        for (const key of newPool.keys()) {
          if (key > maxNum) maxNum = key;
        }
        // Track existing URLs to avoid duplicates
        const existingUrls = new Set<string>();
        for (const entry of newPool.values()) {
          if (entry.url) existingUrls.add(entry.url);
        }

        for (const event of events) {
          if (event.type === "data_fetch_complete") {
            const results = (event.results as Array<{ title: string; snippet: string; url: string; key_facts?: string }>) ?? [];
            for (const r of results) {
              if (r.url && existingUrls.has(r.url)) continue;
              maxNum++;
              newPool.set(maxNum, {
                title: r.title,
                snippet: r.snippet,
                url: r.url,
                keyFacts: parseKeyFacts(r.key_facts),
              });
              if (r.url) existingUrls.add(r.url);
            }
          }
          if (event.type === "user_data_added") {
            const data = event.data as { title?: string; snippet?: string; content?: string; url?: string };
            if (data) {
              const url = data.url || "";
              if (url && existingUrls.has(url)) continue;
              maxNum++;
              newPool.set(maxNum, {
                title: data.title || "",
                snippet: data.snippet || data.content || "",
                url,
              });
              if (url) existingUrls.add(url);
            }
          }
        }
        return newPool;
      });
    }

    poolProcessedRef.current = events.length;
  }, [events]);

  // Process message events from SSE
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

      if (event.type === "search_queries") {
        const round = event.round as number;
        const queries = (event.queries as string[]) ?? [];
        setMessages((prev) => {
          const next = new Map(prev);
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

      if (event.type === "agent_thinking") {
        const agentId = event.agent as string;
        const round = event.round as number;
        const thinkKey = `${agentId}_${round}`;
        const thinking = event.thinking as string;
        const agentName = (event.agent_name as string) ?? "";

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
    // Use positionColorMap (built from initialPositions order) first
    const mapped = positionColorMap.get(positionId);
    if (mapped !== undefined) return mapped;
    // Fallback: extract digits from ID for SSE events arriving before positions load
    const index = parseInt(positionId.replace(/\D/g, ""), 10);
    return isNaN(index) ? 0 : index % POSITION_COLORS.length;
  }

  const tooltipRootRef = useRef<HTMLDivElement>(null);

  return (
    <div className="relative h-full overflow-hidden">
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
                  <details
                    open={msg.thinkingExpanded}
                    className="mb-3"
                    onToggle={(e) => {
                      const open = (e.target as HTMLDetailsElement).open;
                      if (open !== msg.thinkingExpanded) {
                        setMessages((prev) => {
                          const next = new Map(prev);
                          next.set(msg.id, { ...msg, thinkingExpanded: open });
                          return next;
                        });
                      }
                    }}
                  >
                    <summary className="text-xs font-medium text-amber-400/90 cursor-pointer select-none flex items-center gap-1.5">
                      <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500/70" />
                      思考过程
                      {!msg.complete && (
                        <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                      )}
                    </summary>
                    <div className="mt-2 pl-3 border-l-2 border-amber-700/40 text-sm text-amber-200/60 leading-relaxed whitespace-pre-wrap max-h-40 overflow-y-auto">
                      {msg.thinking}
                    </div>
                  </details>
                )}
                <p className="text-neutral-300 text-sm leading-relaxed whitespace-pre-wrap">
                  <CitationText text={msg.content} poolMap={dataPool} tooltipRootRef={tooltipRootRef} />
                </p>
              </>
            )}
          </div>
        );
      })}
    </div>
    <div ref={tooltipRootRef} className="absolute inset-0 pointer-events-none z-50 overflow-hidden" />
    </div>
  );
}
