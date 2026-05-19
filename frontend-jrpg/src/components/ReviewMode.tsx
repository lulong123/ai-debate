import { useEffect, useMemo, useRef } from "react";
import type { SSEEvent } from "../hooks/useSSE";
import { CitationText } from "./CitationText";
import type { DataPoolEntry } from "./CitationText";

/* ─── 角色颜色 ─── */

const AGENT_COLORS: Record<string, string> = {
  moderator: "text-amber-400",
  data_clerk: "text-emerald-400",
  scorer: "text-violet-400",
};

const DEBATER_COLORS = [
  "text-emerald-400", "text-amber-400", "text-red-400",
  "text-violet-400", "text-pink-400", "text-cyan-400",
];

function getAgentColor(agentId: string, positionId?: string): string {
  if (agentId === "moderator") return AGENT_COLORS.moderator;
  if (agentId === "data_clerk") return AGENT_COLORS.data_clerk;
  if (agentId === "scorer") return AGENT_COLORS.scorer;
  const idx = parseInt(positionId?.replace("position_", "") ?? "0", 10) || 0;
  return DEBATER_COLORS[idx % DEBATER_COLORS.length];
}

function getRoleLabel(agentId: string): string {
  if (agentId === "moderator") return "主持人";
  if (agentId === "data_clerk") return "研究员";
  if (agentId === "scorer") return "评委";
  return "辩手";
}

/* ─── 消息类型 ─── */

interface ParsedMessage {
  agentId: string;
  agentName: string;
  positionId?: string;
  content: string;
  isThinking?: boolean;
  isScore?: boolean;
  scores?: Record<string, number>;
  roundNumber?: number;
  timestamp: number;
}

/* ─── Review 模式组件 ─── */

interface ReviewModeProps {
  events: SSEEvent[];
}

export default function ReviewMode({ events }: ReviewModeProps) {
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

  // Parse events into messages
  const messages = useMemo(() => {
    const msgs: ParsedMessage[] = [];
    for (const ev of events) {
      switch (ev.type) {
        case "discussion_start":
          msgs.push({
            agentId: "moderator",
            agentName: "主持人",
            content: (ev.opening as string) ?? "辩论开始！",
            roundNumber: 0,
            timestamp: Date.now(),
          });
          break;
        case "agent_message_complete":
          msgs.push({
            agentId: (ev.agent_id as string) ?? "",
            agentName: (ev.agent_name as string) ?? "",
            positionId: (ev.position_id as string) ?? undefined,
            content: (ev.content as string) ?? "",
            roundNumber: (ev.round_number as number) ?? undefined,
            timestamp: Date.now(),
          });
          break;
        case "moderator_guidance":
          msgs.push({
            agentId: "moderator",
            agentName: "主持人",
            content: (ev.content as string) ?? "",
            roundNumber: (ev.round_number as number) ?? undefined,
            timestamp: Date.now(),
          });
          break;
        case "agent_thinking":
          msgs.push({
            agentId: (ev.agent_id as string) ?? "",
            agentName: (ev.agent_name as string) ?? "",
            positionId: (ev.position_id as string) ?? undefined,
            content: (ev.content as string) ?? "",
            isThinking: true,
            timestamp: Date.now(),
          });
          break;
        case "score_update":
          msgs.push({
            agentId: (ev.agent_id as string) ?? "",
            agentName: (ev.agent_name as string) ?? "",
            content: "",
            isScore: true,
            scores: (ev.scores as Record<string, number>) ?? {},
            timestamp: Date.now(),
          });
          break;
      }
    }
    return msgs;
  }, [events]);

  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-neutral-500">
        暂无消息
      </div>
    );
  }

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      {messages.map((msg, i) => {
        if (msg.isScore) {
          return (
            <div key={i} className="flex justify-center">
              <div className="px-4 py-2 rounded-xl bg-amber-500/10 border border-amber-500/20 text-sm">
                <span className="text-amber-400 font-semibold">⭐ {msg.agentName}</span>
                <span className="text-neutral-400 ml-2">
                  {Object.entries(msg.scores ?? {}).map(([k, v]) => `${k}: ${v}`).join(" · ")}
                </span>
              </div>
            </div>
          );
        }

        if (msg.isThinking) {
          return (
            <div key={i} className="ml-8 pl-4 border-l-2 border-amber-500/30">
              <div className="text-xs text-amber-400/60 mb-1">💭 思考过程</div>
              <p className="text-sm text-neutral-500 leading-relaxed">{msg.content}</p>
            </div>
          );
        }

        const color = getAgentColor(msg.agentId, msg.positionId);
        return (
          <div key={i} className="space-y-1">
            <div className="flex items-center gap-2">
              <span className={`text-sm font-semibold ${color}`}>{msg.agentName}</span>
              <span className="text-xs text-neutral-600">{getRoleLabel(msg.agentId)}</span>
              {msg.roundNumber != null && msg.roundNumber > 0 && (
                <span className="text-xs text-neutral-700">R{msg.roundNumber}</span>
              )}
            </div>
            <div className="text-neutral-200 text-sm leading-relaxed pl-1">
              <CitationText text={msg.content} poolMap={poolMap} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
