import { useEffect, useRef, useState } from "react";
import type { SSEEvent } from "../hooks/useSSE";
import { addUserData } from "../lib/api";

interface DataItem {
  id: string;
  source: "data_clerk" | "user";
  title: string;
  snippet: string;
  url: string;
  round_number: number | null;
  agent_name?: string;
  citationNum?: number;
  keyFacts?: string[];
}

interface DataPoolPanelProps {
  sessionId: string;
  events: SSEEvent[];
  isActive: boolean;
}

export function DataPoolPanel({ sessionId, events, isActive }: DataPoolPanelProps) {
  const [items, setItems] = useState<DataItem[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const processedRef = useRef(0);
  const citationCounter = useRef(0);

  useEffect(() => {
    const newEvents = events.slice(processedRef.current);
    processedRef.current = events.length;

    for (const event of newEvents) {
      if (event.type === "data_fetch_complete") {
        const results = (event.results as Array<{ title: string; snippet: string; url: string; key_facts?: string }>) ?? [];
        const round = event.round as number;
        const agentName = event.agent_name as string;
        const newItems: DataItem[] = results.map((r, i) => {
          let keyFacts: string[] | undefined;
          if (r.key_facts) {
            try {
              const parsed = JSON.parse(r.key_facts);
              keyFacts = parsed.key_facts;
            } catch { /* ignore */ }
          }
          return {
            id: `dc_${round}_${event.message_id}_${i}`,
            source: "data_clerk" as const,
            title: r.title,
            snippet: r.snippet,
            url: r.url,
            round_number: round,
            agent_name: agentName,
            citationNum: ++citationCounter.current,
            keyFacts,
          };
        });
        setItems((prev) => [...prev, ...newItems]);
      }

      if (event.type === "user_data_added") {
        const data = event.data as DataItem;
        if (data) {
          setItems((prev) => [...prev, {
            id: data.id || `user_${Date.now()}`,
            source: "user",
            title: data.title,
            snippet: data.snippet,
            url: data.url,
            round_number: data.round_number,
            citationNum: ++citationCounter.current,
          }]);
        }
      }
    }
  }, [events]);

  if (!isActive) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;
    setSubmitting(true);
    try {
      await addUserData(sessionId, title.trim(), content.trim(), url.trim() || undefined);
      setTitle("");
      setContent("");
      setUrl("");
    } catch {
      // Error is silent, item will appear via SSE or not at all
    } finally {
      setSubmitting(false);
    }
  }

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Group items by round
  const roundGroups: Record<string, DataItem[]> = {};
  for (const item of items) {
    const key = item.round_number === null ? "user" : `round_${item.round_number}`;
    if (!roundGroups[key]) roundGroups[key] = [];
    roundGroups[key].push(item);
  }

  const debateActive = events.some((e) => e.type === "discussion_start") &&
    !events.some((e) => e.type === "discussion_end" || e.type === "error");

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto pr-1 space-y-3">
        {Object.entries(roundGroups).map(([key, groupItems]) => (
          <div key={key}>
            <div className="text-xs text-neutral-500 font-medium mb-1 uppercase tracking-wider">
              {key === "user" ? "用户添加" : `第 ${key.replace("round_", "")} 轮`}
            </div>
            {groupItems.map((item) => {
              const isExpanded = expanded.has(item.id);
              const isUser = item.source === "user";
              return (
                <div
                  key={item.id}
                  className={`p-2 rounded-lg mb-1 text-xs ${
                    isUser
                      ? "bg-amber-950/20 border border-amber-900/30"
                      : "bg-cyan-950/20 border border-cyan-900/30"
                  }`}
                >
                  <div className="flex items-start gap-1">
                    {item.citationNum && (
                      <span className="shrink-0 w-4 h-4 flex items-center justify-center text-[10px] font-bold bg-blue-600/80 text-white rounded-full">
                        {item.citationNum}
                      </span>
                    )}
                    <span className={`shrink-0 px-1 py-0.5 rounded text-[10px] font-medium ${
                      isUser ? "bg-amber-900/40 text-amber-300" : "bg-cyan-900/40 text-cyan-300"
                    }`}>
                      {isUser ? "用户" : item.agent_name || "搜索"}
                    </span>
                    <span className="font-medium text-neutral-300">{item.title}</span>
                  </div>
                  {item.keyFacts && item.keyFacts.length > 0 ? (
                    <ul className={`text-neutral-400 mt-1 space-y-0.5 ${!isExpanded ? "line-clamp-3" : ""}`}
                        onClick={() => toggleExpand(item.id)}
                    >
                      {item.keyFacts.slice(0, isExpanded ? undefined : 2).map((fact, fi) => (
                        <li key={fi} className="flex gap-1 text-xs">
                          <span className="text-amber-400 shrink-0">•</span>
                          <span>{fact.length > 80 ? fact.slice(0, 80) + "..." : fact}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p
                      className={`text-neutral-400 mt-1 cursor-pointer ${!isExpanded ? "line-clamp-2" : ""}`}
                      onClick={() => toggleExpand(item.id)}
                    >
                      {item.snippet}
                    </p>
                  )}
                  {item.url && (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-cyan-500/70 hover:text-cyan-400 mt-1 inline-flex items-center gap-0.5"
                    >
                      <span className="truncate max-w-[200px]">{item.url}</span>
                      <svg className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        ))}

        {items.length === 0 && (
          <p className="text-xs text-neutral-600 text-center py-4">
            暂无数据，辩论开始后将自动搜索
          </p>
        )}
      </div>

      {/* User data contribution form */}
      {debateActive && (
        <form onSubmit={handleSubmit} className="border-t border-neutral-800 pt-3 mt-3 space-y-2">
          <div className="text-xs text-neutral-500 font-medium">添加数据</div>
          <input
            type="text"
            placeholder="标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-xs text-neutral-300 placeholder-neutral-600 focus:border-neutral-700 focus:outline-none"
            required
            maxLength={200}
          />
          <textarea
            placeholder="内容"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-xs text-neutral-300 placeholder-neutral-600 focus:border-neutral-700 focus:outline-none resize-none"
            rows={2}
            required
            maxLength={2000}
          />
          <input
            type="url"
            placeholder="URL（可选）"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-xs text-neutral-300 placeholder-neutral-600 focus:border-neutral-700 focus:outline-none"
            maxLength={500}
          />
          <button
            type="submit"
            disabled={submitting || !title.trim() || !content.trim()}
            className="w-full bg-cyan-700 hover:bg-cyan-600 disabled:bg-neutral-800 disabled:text-neutral-600 text-white text-xs py-1.5 rounded transition-colors"
          >
            {submitting ? "提交中..." : "添加"}
          </button>
        </form>
      )}
    </div>
  );
}
