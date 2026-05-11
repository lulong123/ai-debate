import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSession, clarifyTopic, refineTopic } from "../lib/api";

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
  } | null>(null);

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
            if (event.valid) {
              navigate(`/positions/${session_id}`);
            } else {
              setClarification({ question: event.question, sessionId: session_id });
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
    try {
      await refineTopic(clarification.sessionId, answer);
      setClarification(null);
      navigate(`/positions/${clarification.sessionId}`);
    } catch {
      setError("提交失败，请检查网络连接后重试");
    } finally {
      setLoading(false);
    }
  }

  if (clarification) {
    return (
      <div className="max-w-2xl mx-auto">
        <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4 text-blue-400">主持人有疑问</h2>
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
    <div className="max-w-2xl mx-auto px-4 sm:px-0">
      <div className="text-center mb-8">
        <h2 className="text-3xl font-bold mb-2">开始一场辩论</h2>
        <p className="text-neutral-400">
          提交一个问题，让多个 AI 立场辩论对决，帮你做出更好的判断
        </p>
      </div>

      <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-6">
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
          <div className="mt-4 space-y-2">
            {thinkingSteps.map((step, i) => (
              <div key={`step-${i}`} className="flex items-center gap-2 text-sm text-neutral-400">
                <div
                  className={`w-1.5 h-1.5 rounded-full ${
                    i === thinkingSteps.length - 1
                      ? "bg-blue-500 animate-pulse"
                      : "bg-neutral-600"
                  }`}
                />
                <span>{step.message}</span>
              </div>
            ))}
            {thinkingContent && (
              <details
                open
                className="mt-3 p-3 rounded-lg border border-amber-700/40 bg-amber-950/20"
              >
                <summary className="text-sm font-medium text-amber-400 cursor-pointer">
                  主持人思考中...
                </summary>
                <p className="mt-2 text-xs text-amber-200/70 whitespace-pre-wrap leading-relaxed">
                  {thinkingContent}
                </p>
              </details>
            )}
            {searchSteps.length > 0 && (
              <details
                open
                className="mt-3 p-3 rounded-lg border border-cyan-800/40 bg-cyan-950/20"
              >
                <summary className="text-sm font-medium text-cyan-400 cursor-pointer">
                  搜索结果（{searchSteps.reduce((acc, s) => acc + s.results.length, 0)} 条）
                </summary>
                <div className="mt-2 space-y-2">
                  {searchSteps.map((step, si) => (
                    <div key={`search-${si}`}>
                      <div className="text-xs text-cyan-500/70 mb-1">
                        关键词：{step.queries.join("、")}
                      </div>
                      {step.results.map((r, ri) => (
                        <div key={ri} className="text-xs ml-2 mb-1">
                          <span className="text-cyan-400/70 font-medium">{r.title}</span>
                          <span className="mx-1 text-neutral-600">-</span>
                          <span className="text-neutral-400">
                            {r.snippet.slice(0, 120)}{r.snippet.length > 120 ? "..." : ""}
                          </span>
                          {r.url && (
                            <a
                              href={r.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-1 text-cyan-500/50 hover:text-cyan-400"
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
