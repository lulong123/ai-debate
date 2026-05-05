import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useSSE } from "../hooks/useSSE";
import { ChatStream } from "../components/ChatStream";
import { ScorePanel } from "../components/ScorePanel";

export function Discussion() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const { events, connected } = useSSE(sessionId || null);
  const [showScores, setShowScores] = useState(true);

  const status = useMemo(() => {
    const lastEvent = events[events.length - 1];
    if (!lastEvent) return "waiting";
    if (lastEvent.type === "discussion_end") return "completed";
    if (lastEvent.type === "error") return "error";
    return "discussing";
  }, [events]);

  const currentRound = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "round_start") return events[i].round as number;
    }
    return 0;
  }, [events]);

  // Missing session ID guard
  if (!sessionId) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4">
        <p className="text-neutral-400">无效的讨论链接</p>
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
              ? `讨论进行中 · 第 ${currentRound} 轮`
              : status === "completed"
              ? "讨论已结束"
              : status === "error"
              ? "讨论出错"
              : "等待开始..."}
          </span>
          <span className="text-xs text-neutral-600">
            {connected ? "已连接" : "连接中..."}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowScores(!showScores)}
            className="text-xs text-neutral-500 hover:text-neutral-300 px-2 py-1 border border-neutral-800 rounded"
          >
            {showScores ? "隐藏评分" : "显示评分"}
          </button>
          {status === "completed" && (
            <button
              onClick={() => navigate(`/minutes/${sessionId}`)}
              className="text-xs bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded"
            >
              查看纪要
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
            {(events[events.length - 1]?.message as string) || "讨论过程中发生错误"}
          </p>
          <p className="text-xs text-red-400/70 mt-1">你可以返回首页开始新的讨论</p>
        </div>
      )}

      {/* Main content - responsive layout */}
      <div className="flex flex-col md:flex-row gap-4 flex-1 min-h-0">
        {/* Chat area */}
        <div className="flex-1 min-w-0">
          <ChatStream events={events} />
        </div>

        {/* Score panel - hidden on small screens unless toggled */}
        {showScores && (
          <div className="w-full md:w-72 shrink-0 overflow-y-auto max-h-[40vh] md:max-h-none">
            <ScorePanel events={events} />
          </div>
        )}
      </div>
    </div>
  );
}
