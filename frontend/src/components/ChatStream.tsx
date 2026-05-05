import { useEffect, useRef, useState } from "react";
import type { SSEEvent } from "../hooks/useSSE";

const ANGLE_COLORS = [
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
  angleId: string;
  content: string;
  round: number;
  conceded: boolean;
  complete: boolean;
}

interface ChatStreamProps {
  events: SSEEvent[];
}

export function ChatStream({ events }: ChatStreamProps) {
  const [messages, setMessages] = useState<Map<string, AgentMessage>>(new Map());
  const [displayOrder, setDisplayOrder] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    for (const event of events) {
      if (event.type === "discussion_start") {
        // Add opening as moderator message
        const id = "opening";
        setMessages((prev) => {
          const next = new Map(prev);
          next.set(id, {
            id,
            agentName: "主持人",
            angleId: "moderator",
            content: event.opening as string,
            round: 0,
            conceded: false,
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
            angleId: event.agent as string,
            content: "",
            round: event.round as number,
            conceded: false,
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
              conceded: event.conceded as boolean,
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
            angleId: "moderator",
            content: event.content as string,
            round: event.round as number,
            conceded: false,
            complete: true,
          });
          return next;
        });
        setDisplayOrder((prev) => prev.includes(id) ? prev : [...prev, id]);
      }
    }
  }, [events]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  function getAngleColorIndex(angleId: string): number {
    const index = parseInt(angleId.replace(/\D/g, ""), 10);
    return isNaN(index) ? 0 : index % ANGLE_COLORS.length;
  }

  return (
    <div ref={scrollRef} className="flex flex-col gap-3 overflow-y-auto h-full pr-2">
      {displayOrder.map((id) => {
        const msg = messages.get(id);
        if (!msg) return null;

        const isModerator = msg.angleId === "moderator";
        const colorClass = isModerator
          ? "text-blue-400"
          : ANGLE_COLORS[getAngleColorIndex(msg.angleId)];

        return (
          <div
            key={id}
            className={`p-4 rounded-xl ${
              isModerator ? "bg-blue-950/30 border border-blue-900/50" : "bg-neutral-900"
            }`}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className={`font-semibold text-sm ${colorClass}`}>
                {msg.agentName}
              </span>
              <span className="text-xs text-neutral-600">
                第 {msg.round} 轮
              </span>
              {msg.conceded && (
                <span className="text-xs bg-neutral-800 text-neutral-400 px-2 py-0.5 rounded">
                  已认输
                </span>
              )}
              {!msg.complete && (
                <span className="inline-block w-1.5 h-4 bg-current animate-pulse" />
              )}
            </div>
            <p className="text-neutral-300 text-sm leading-relaxed whitespace-pre-wrap">
              {msg.content}
            </p>
          </div>
        );
      })}
    </div>
  );
}
