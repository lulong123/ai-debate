import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useSSE } from "../hooks/useSSE";
import { ChatStream } from "../components/ChatStream";
import { DataPoolPanel } from "../components/DataPoolPanel";
import {
  getDataPool,
  getMessages,
  getPositions,
  type DataPoolItem,
  type MessageResponse,
  type PositionItem,
} from "../lib/api";

export function Discussion() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const { events, connected } = useSSE(sessionId || null);
  const [showDataPool, setShowDataPool] = useState(true);

  // History from API (for completed / reconnect)
  const [initialMessages, setInitialMessages] = useState<MessageResponse[] | null>(null);
  const [initialPool, setInitialPool] = useState<DataPoolItem[] | null>(null);
  const [initialPositions, setInitialPositions] = useState<PositionItem[] | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    Promise.all([
      getMessages(sessionId).catch(() => []),
      getDataPool(sessionId).catch(() => []),
      getPositions(sessionId).catch(() => []),
    ]).then(([msgs, pool, pos]) => {
      setInitialMessages(msgs);
      setInitialPool(pool);
      setInitialPositions(pos);
    });
  }, [sessionId]);

  const status = useMemo(() => {
    const lastEvent = events[events.length - 1];
    if (!lastEvent && initialMessages !== null) {
      // No SSE events yet — infer from persisted messages
      return initialMessages.length > 0 ? "completed" : "waiting";
    }
    if (!lastEvent) return "waiting";
    if (lastEvent.type === "discussion_end") return "completed";
    if (lastEvent.type === "error") return "error";
    return "discussing";
  }, [events, initialMessages]);

  const currentRound = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "round_start") return events[i].round as number;
    }
    // Fallback: derive from persisted messages
    if (initialMessages) {
      let maxRound = 0;
      for (const m of initialMessages) {
        if (m.round_number && m.round_number > maxRound) maxRound = m.round_number;
      }
      return maxRound;
    }
    return 0;
  }, [events, initialMessages]);

  if (!sessionId) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4">
        <p className="text-neutral-400">无效的链接</p>
        <button
          onClick={() => navigate("/")}
          className="text-sm text-blue-400 hover:text-blue-300"
        >
          返回首页
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-120px)] overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center justify-between mb-4 px-1 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <div
            className={`w-2 h-2 rounded-full ${
              status === "discussing"
                ? "bg-green-500 animate-pulse"
                : status === "completed"
                ? "bg-blue-500"
                : status === "error"
                ? "bg-red-500"
                : "bg-neutral-500"
            }`}
          />
          <span className="text-sm text-neutral-400">
            {status === "discussing"
              ? `辩论进行中 · 第 ${currentRound} 轮`
              : status === "completed"
              ? "辩论已结束"
              : status === "error"
              ? "辩论出错"
              : "等待开始..."}
          </span>
          <span className="text-xs text-neutral-600">
            {connected ? "已连接" : "连接中..."}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowDataPool(!showDataPool)}
            className="text-xs text-neutral-500 hover:text-neutral-300 px-2 py-1 border border-neutral-800 rounded"
          >
            {showDataPool ? "隐藏数据池" : "数据池"}
          </button>
          {status === "completed" && (
            <button
              onClick={() => navigate(`/minutes/${sessionId}`)}
              className="text-xs bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded"
            >
              查看裁决
            </button>
          )}
          {status === "error" && (
            <button
              onClick={() => navigate("/")}
              className="text-xs bg-neutral-700 hover:bg-neutral-600 text-white px-3 py-1 rounded"
            >
              返回首页
            </button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {status === "error" && (
        <div className="bg-red-950/50 border border-red-900/50 rounded-lg p-3 mb-3">
          <p className="text-sm text-red-300">
            {(events[events.length - 1]?.message as string) || "辩论过程中发生错误"}
          </p>
          <p className="text-xs text-red-400/70 mt-1">你可以返回首页开始新的辩论</p>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-col md:flex-row gap-4 flex-1 min-h-0">
        <div className="flex-1 min-w-0">
          <ChatStream
            events={events}
            initialMessages={initialMessages}
            initialPool={initialPool}
            initialPositions={initialPositions}
          />
        </div>
        {showDataPool && sessionId && (
          <div className="w-full md:w-80 shrink-0 overflow-y-auto max-h-[40vh] md:max-h-none">
            <DataPoolPanel
              sessionId={sessionId}
              events={events}
              initialPool={initialPool}
              isActive={showDataPool}
            />
          </div>
        )}
      </div>
    </div>
  );
}
