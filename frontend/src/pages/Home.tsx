import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSession, clarifyTopic, refineTopic, listSessions, type SessionListItem } from "../lib/api";
import { SessionCard } from "../components/SessionCard";

interface ThinkingStep {
  step: string;
  message: string;
}

interface SearchResult {
  title: string;
  snippet: string;
  url: string;
}

interface SearchStep {
  queries: string[];
  results: SearchResult[];
}

export function Home() {
  const navigate = useNavigate();
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
  const [thinkingContent, setThinkingContent] = useState("");
  const [searchSteps, setSearchSteps] = useState<SearchStep[]>([]);
  const [clarification, setClarification] = useState<{
    question: string;
    sessionId: string;
    round: number;
    needDataClerk: boolean;
  } | null>(null);
  const [rejected, setRejected] = useState<{ reason: string } | null>(null);

  // History
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");

  async function loadHistory(search?: string) {
    setHistoryLoading(true);
    try {
      const data = await listSessions({ search: search || undefined, limit: 50 });
      setSessions(data);
    } catch {
      // ignore
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    loadHistory();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => loadHistory(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  function handleSessionDeleted() {
    loadHistory(searchQuery);
  }

  function handleSessionRenamed(id: string, newTopic: string) {
    setSessions((prev) =>
      prev.map((s) => (s.session_id === id ? { ...s, topic: newTopic } : s))
    );
  }

  async function handleSubmit() {
    if (!topic.trim()) return;
    setLoading(true);
    setError("");
    setThinkingSteps([]);
    setThinkingContent("");
    try {
      const { session_id } = await createSession(topic.trim());

      const abortController = new AbortController();

      // Connect SSE to receive progress events AND final result
      const es = new EventSource(`/api/sessions/${session_id}/stream`);

      // Track pending search queries waiting for their results
      let pendingQueries: string[] = [];

      const cleanup = () => {
        abortController.abort();
        es.close();
      };

      // Progress events (analysis_progress, agent_thinking, search)
      const handleProgress = (e: MessageEvent) => {
        if (abortController.signal.aborted || !e.data) return;
        try {
          const event = JSON.parse(e.data as string);
          if (event.type === "analysis_progress") {
            setThinkingSteps((prev) => [
              ...prev,
              { step: event.step, message: event.message },
            ]);
          } else if (event.type === "agent_thinking") {
            setThinkingContent(event.thinking || "");
          } else if (event.type === "search_queries") {
            pendingQueries = event.queries || [];
            // Add a step with the queries
            setThinkingSteps((prev) => [
              ...prev,
              { step: "search_queries", message: `搜索关键词：${(event.queries || []).join("、")}` },
            ]);
          } else if (event.type === "search_results") {
            const results: SearchResult[] = event.results || [];
            setSearchSteps((prev) => [
              ...prev,
              { queries: pendingQueries, results },
            ]);
            pendingQueries = [];
          }
        } catch {
          // Ignore non-JSON messages
        }
      };

      es.addEventListener("analysis_progress", handleProgress);
      es.addEventListener("agent_thinking", handleProgress);
      es.addEventListener("search_queries", handleProgress);
      es.addEventListener("search_results", handleProgress);

      // Result event from SSE
      const handleResult = (e: MessageEvent) => {
        if (abortController.signal.aborted || !e.data) return;
        try {
          const event = JSON.parse(e.data as string);
          if (event.type === "clarify_result") {
            cleanup();
            setLoading(false);
            if (event.rejected) {
              setRejected({ reason: event.reason || "议题不适合辩论" });
            } else if (event.valid) {
              const clerkParam = event.need_data_clerk ? "?data_clerk=1" : "";
              navigate(`/positions/${session_id}${clerkParam}`);
            } else {
              setClarification({
                question: event.question,
                sessionId: session_id,
                round: event.clarify_round || 1,
                needDataClerk: event.need_data_clerk || false,
              });
            }
          } else if (event.type === "error" && event.source === "clarify") {
            cleanup();
            setLoading(false);
            setError("主题分析失败，请重试");
          }
        } catch {
          // Ignore non-JSON messages
        }
      };

      es.addEventListener("clarify_result", handleResult);
      es.addEventListener("error", handleResult);

      // Wait for SSE connection to be ready before calling clarify
      await new Promise<void>((resolve) => {
        es.onopen = () => resolve();
        setTimeout(() => resolve(), 2000); // fallback timeout
      });

      if (abortController.signal.aborted) return;

      // Fire-and-forget: result comes via SSE
      clarifyTopic(session_id).catch(() => {
        if (abortController.signal.aborted) return;
        cleanup();
        setLoading(false);
        setError("请求失败，请检查网络连接后重试");
      });
    } catch {
      setError("创建失败，请检查网络连接后重试");
      setLoading(false);
    }
  }

  async function handleClarifyAnswer(answer: string) {
    if (!clarification) return;
    setLoading(true);
    setError("");
    setThinkingSteps([]);
    setThinkingContent("");
    setSearchSteps([]);
    try {
      await refineTopic(clarification.sessionId, answer);
      // Re-trigger clarify with updated topic
      const es = new EventSource(`/api/sessions/${clarification.sessionId}/stream`);
      const abortController = new AbortController();
      let pendingQueries: string[] = [];

      const cleanup = () => { abortController.abort(); es.close(); };

      const handleProgress = (e: MessageEvent) => {
        if (abortController.signal.aborted || !e.data) return;
        try {
          const event = JSON.parse(e.data as string);
          if (event.type === "analysis_progress") {
            setThinkingSteps((prev) => [...prev, { step: event.step, message: event.message }]);
          } else if (event.type === "agent_thinking") {
            setThinkingContent(event.thinking || "");
          } else if (event.type === "search_queries") {
            pendingQueries = event.queries || [];
            setThinkingSteps((prev) => [...prev, { step: "search_queries", message: `搜索关键词：${(event.queries || []).join("、")}` }]);
          } else if (event.type === "search_results") {
            const results: SearchResult[] = event.results || [];
            setSearchSteps((prev) => [...prev, { queries: pendingQueries, results }]);
            pendingQueries = [];
          }
        } catch { /* ignore */ }
      };

      es.addEventListener("analysis_progress", handleProgress);
      es.addEventListener("agent_thinking", handleProgress);
      es.addEventListener("search_queries", handleProgress);
      es.addEventListener("search_results", handleProgress);

      const handleResult = (e: MessageEvent) => {
        if (abortController.signal.aborted || !e.data) return;
        try {
          const event = JSON.parse(e.data as string);
          if (event.type === "clarify_result") {
            cleanup();
            setLoading(false);
            if (event.rejected) {
              setClarification(null);
              setRejected({ reason: event.reason || "议题不适合辩论" });
            } else if (event.valid) {
              setClarification(null);
              const clerkParam = event.need_data_clerk ? "?data_clerk=1" : "";
              navigate(`/positions/${clarification.sessionId}${clerkParam}`);
            } else {
              // Still not clear — update question for next round
              setClarification({
                question: event.question,
                sessionId: clarification.sessionId,
                round: event.clarify_round || clarification.round + 1,
                needDataClerk: event.need_data_clerk || false,
              });
            }
          } else if (event.type === "error" && event.source === "clarify") {
            cleanup();
            setLoading(false);
            setError("主题分析失败，请重试");
          }
        } catch { /* ignore */ }
      };

      es.addEventListener("clarify_result", handleResult);
      es.addEventListener("error", handleResult);

      await new Promise<void>((resolve) => {
        es.onopen = () => resolve();
        setTimeout(() => resolve(), 2000);
      });
      if (abortController.signal.aborted) return;
      clarifyTopic(clarification.sessionId).catch(() => {
        if (abortController.signal.aborted) return;
        cleanup();
        setLoading(false);
        setError("请求失败，请检查网络连接后重试");
      });
    } catch {
      setError("提交失败，请检查网络连接后重试");
      setLoading(false);
    }
  }

  if (rejected) {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="bg-red-950/30 border border-red-800/50 rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4 text-red-400">议题被拒绝</h2>
          <p className="text-red-300 mb-6">{rejected.reason}</p>
          {error && (
            <div className="bg-red-950/50 border border-red-900/50 rounded-lg p-3 mb-4">
              <p className="text-sm text-red-300">{error}</p>
            </div>
          )}
          <button
            onClick={() => { setRejected(null); setTopic(""); setError(""); }}
            className="bg-neutral-700 hover:bg-neutral-600 text-white px-4 py-2 rounded-lg"
          >
            返回重新提交
          </button>
        </div>
      </div>
    );
  }

  if (clarification) {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold text-blue-400 mb-4">主持人有疑问</h2>
          <p className="text-neutral-300 mb-6">{clarification.question}</p>
          {error && (
            <div className="bg-red-950/50 border border-red-900/50 rounded-lg p-3 mb-4">
              <p className="text-sm text-red-300">{error}</p>
            </div>
          )}
          <ClarifyInput onSubmit={handleClarifyAnswer} loading={loading} />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-0">
      {/* New debate input */}
      <div className="text-center mb-8">
        <h2 className="text-3xl font-bold mb-2">开始一场辩论</h2>
        <p className="text-neutral-400">
          提交一个问题，让多个 AI 立场辩论对决，帮你做出更好的判断
        </p>
      </div>

      <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-8">
        {error && (
          <div className="bg-red-950/50 border border-red-900/50 rounded-lg p-3 mb-4">
            <p className="text-sm text-red-300">{error}</p>
          </div>
        )}
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={"输入你想辩论的问题...\n\n例如：谁是历史第一足球运动员？"}
          className="w-full h-32 bg-neutral-800 border border-neutral-700 rounded-lg p-4 text-neutral-100 placeholder-neutral-500 resize-none focus:outline-none focus:border-blue-500"
          disabled={loading}
        />
        <button
          onClick={handleSubmit}
          disabled={loading || !topic.trim()}
          className="mt-4 w-full bg-blue-600 hover:bg-blue-700 disabled:bg-neutral-700 disabled:text-neutral-500 text-white font-medium py-3 rounded-lg transition-colors"
        >
          {loading ? "分析中..." : "提交问题"}
        </button>

        {/* Thinking progress during clarify */}
        {loading && (thinkingSteps.length > 0 || thinkingContent || searchSteps.length > 0) && (
          <div className="mt-4">
            {/* Timeline steps */}
            <div className="space-y-0">
              {thinkingSteps.map((step, i) => {
                const isLast = i === thinkingSteps.length - 1 && !thinkingContent && searchSteps.length === 0;
                const isDone = i < thinkingSteps.length - 1;
                return (
                  <div key={`step-${i}`} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <div
                        className={`w-2.5 h-2.5 rounded-full mt-1.5 shrink-0 ${
                          isLast ? "bg-blue-500 animate-pulse" : isDone ? "bg-blue-500/60" : "bg-neutral-600"
                        }`}
                      />
                      {i < thinkingSteps.length - 1 && (
                        <div className="w-px flex-1 bg-neutral-700/50 min-h-3" />
                      )}
                    </div>
                    <span className={`text-sm pb-3 ${isDone ? "text-neutral-500" : "text-neutral-300"}`}>
                      {step.message}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Thinking content - collapsed by default */}
            {thinkingContent && (
              <details className="mt-2 p-3 rounded-lg border border-amber-700/30 bg-amber-950/15">
                <summary className="text-xs font-medium text-amber-400/80 cursor-pointer select-none">
                  主持人思考过程
                </summary>
                <div className="mt-2 text-xs text-amber-200/60 whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto">
                  {thinkingContent}
                </div>
              </details>
            )}

            {/* Search results - collapsed by default */}
            {searchSteps.length > 0 && (
              <details className="mt-2 p-3 rounded-lg border border-cyan-800/30 bg-cyan-950/15">
                <summary className="text-xs font-medium text-cyan-400/80 cursor-pointer select-none">
                  搜索数据（{searchSteps.reduce((acc, s) => acc + s.results.length, 0)} 条）
                </summary>
                <div className="mt-2 space-y-2 max-h-60 overflow-y-auto">
                  {searchSteps.map((step, si) => (
                    <div key={`search-${si}`}>
                      <div className="text-xs text-cyan-500/60 mb-1">
                        关键词：{step.queries.join("、")}
                      </div>
                      {step.results.map((r, ri) => (
                        <div key={ri} className="text-xs ml-2 mb-1.5">
                          <span className="text-cyan-400/60 font-medium">{r.title}</span>
                          <span className="mx-1 text-neutral-600">-</span>
                          <span className="text-neutral-400">
                            {r.snippet.slice(0, 120)}{r.snippet.length > 120 ? "..." : ""}
                          </span>
                          {r.url && (
                            <a
                              href={r.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-1 text-cyan-500/40 hover:text-cyan-400"
                            >
                              [链接]
                            </a>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>

      {/* History section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-neutral-300">历史辩论</h3>
          {sessions.length > 0 && (
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索议题..."
              className="bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-blue-500 w-48"
            />
          )}
        </div>

        {historyLoading ? (
          <div className="text-center py-8 text-neutral-500 text-sm">加载中...</div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-12 text-neutral-600 text-sm">
            {searchQuery ? "没有找到匹配的辩论" : "还没有辩论记录，开始第一场吧"}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {sessions.map((s) => (
              <SessionCard
                key={s.session_id}
                session={s}
                onDeleted={handleSessionDeleted}
                onRenamed={handleSessionRenamed}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ClarifyInput({ onSubmit, loading }: { onSubmit: (v: string) => void; loading: boolean }) {
  const [answer, setAnswer] = useState("");
  return (
    <>
      <textarea
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="请回答主持人的问题..."
        className="w-full h-24 bg-neutral-800 border border-neutral-700 rounded-lg p-4 text-neutral-100 placeholder-neutral-500 resize-none focus:outline-none focus:border-blue-500"
        disabled={loading}
      />
      <button
        onClick={() => onSubmit(answer)}
        disabled={loading || !answer.trim()}
        className="mt-3 w-full bg-blue-600 hover:bg-blue-700 disabled:bg-neutral-700 disabled:text-neutral-500 text-white font-medium py-3 rounded-lg transition-colors"
      >
        {loading ? "提交中..." : "确认"}
      </button>
    </>
  );
}
