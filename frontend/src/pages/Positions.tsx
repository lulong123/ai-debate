import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { suggestPositions, startDiscussion } from "../lib/api";

interface Position {
  id: string;
  name: string;
  description: string;
}

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

export function Positions() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [positions, setPositions] = useState<Position[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [dataClerkReason, setDataClerkReason] = useState("");
  const [enableDataClerk, setEnableDataClerk] = useState(false);
  const [preliminaryData, setPreliminaryData] = useState<
    Array<{ title: string; snippet: string; url: string }> | null
  >(null);
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
  const [thinkingContent, setThinkingContent] = useState("");
  const [searchSteps, setSearchSteps] = useState<SearchStep[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setError("无效的链接");
      setLoading(false);
      return;
    }

    const abortController = new AbortController();

    // Connect SSE to receive progress events
    const es = new EventSource(`/api/sessions/${sessionId}/stream`);
    esRef.current = es;

    // Track pending search queries waiting for their results
    let pendingQueries: string[] = [];

    const handleEvent = (e: MessageEvent) => {
      if (abortController.signal.aborted) return;
      if (!e.data) return;
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

    es.addEventListener("analysis_progress", handleEvent);
    es.addEventListener("agent_thinking", handleEvent);
    es.addEventListener("search_queries", handleEvent);
    es.addEventListener("search_results", handleEvent);

    // Result event from SSE
    const handleResult = (e: MessageEvent) => {
      if (abortController.signal.aborted || !e.data) return;
      try {
        const event = JSON.parse(e.data as string);
        if (event.type === "positions_result") {
          setPositions(event.positions || []);
          setDataClerkReason(event.data_clerk_reason ?? "");
          setEnableDataClerk(event.data_clerk_recommended ?? false);
          setPreliminaryData(event.preliminary_data ?? null);
          es.close();
          esRef.current = null;
          setLoading(false);
        } else if (event.type === "error" && event.source === "suggest") {
          setError("获取立场建议失败，请重试");
          es.close();
          esRef.current = null;
          setLoading(false);
        }
      } catch {
        // Ignore non-JSON messages
      }
    };

    es.addEventListener("positions_result", handleResult);
    es.addEventListener("error", handleResult);

    // Wait for SSE connection, then fire-and-forget suggestPositions
    const doSuggest = async () => {
      await new Promise<void>((resolve) => {
        es.onopen = () => resolve();
        setTimeout(() => resolve(), 2000); // fallback timeout
      });

      if (abortController.signal.aborted) return;

      // Fire-and-forget: result comes via SSE
      suggestPositions(sessionId).catch(() => {
        if (abortController.signal.aborted) return;
        setError("获取立场建议失败，请检查网络连接");
        es.close();
        esRef.current = null;
        setLoading(false);
      });
    };

    doSuggest();

    return () => {
      abortController.abort();
      es.close();
      esRef.current = null;
    };
  }, [sessionId]);

  const togglePosition = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  async function handleStart() {
    if (!sessionId || selected.size < 2) return;
    setStarting(true);
    setError("");
    try {
      await startDiscussion(sessionId, Array.from(selected), undefined, enableDataClerk);
      navigate(`/discussion/${sessionId}`);
    } catch {
      setError("开始辩论失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-2xl mx-auto px-4 sm:px-0">
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold mb-2">选择辩论立场</h2>
          <p className="text-neutral-400">主持人正在分析问题，识别可能立场</p>
        </div>
        <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-6">
          {thinkingSteps.length > 0 || thinkingContent || searchSteps.length > 0 ? (
            <div className="space-y-2">
              {thinkingSteps.map((step, i) => (
                <div
                  key={`step-${i}`}
                  className="flex items-center gap-2 text-sm text-neutral-400"
                >
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
          ) : (
            <div className="flex items-center justify-center py-10">
              <div className="animate-spin w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full" />
              <span className="ml-3 text-neutral-400">正在连接...</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  const colors = [
    "border-emerald-500 bg-emerald-500/10",
    "border-amber-500 bg-amber-500/10",
    "border-red-500 bg-red-500/10",
    "border-violet-500 bg-violet-500/10",
    "border-pink-500 bg-pink-500/10",
    "border-cyan-500 bg-cyan-500/10",
  ];

  return (
    <div className="max-w-2xl mx-auto px-4 sm:px-0">
      <div className="text-center mb-8">
        <h2 className="text-2xl font-bold mb-2">选择辩论立场</h2>
        <p className="text-neutral-400">至少选择 2 个立场开始辩论</p>
      </div>

      {error && (
        <div className="bg-red-950/50 border border-red-900/50 rounded-lg p-3 mb-4">
          <p className="text-sm text-red-300">{error}</p>
          {error.includes("链接") && (
            <button
              onClick={() => navigate("/")}
              className="text-xs text-blue-400 hover:text-blue-300 mt-2"
            >
              返回首页
            </button>
          )}
        </div>
      )}

      <div className="grid gap-3">
        {positions.map((pos, i) => {
          const isSelected = selected.has(pos.id);
          return (
            <button
              key={pos.id}
              onClick={() => togglePosition(pos.id)}
              className={`text-left p-4 rounded-xl border-2 transition-all ${
                isSelected
                  ? colors[i % colors.length]
                  : "border-neutral-700 bg-neutral-900 hover:border-neutral-600"
              }`}
            >
              <div className="flex items-center gap-3">
                <div
                  className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                    isSelected ? "border-current" : "border-neutral-600"
                  }`}
                >
                  {isSelected && <div className="w-3 h-3 rounded-full bg-current" />}
                </div>
                <div>
                  <h3 className="font-semibold">{pos.name}</h3>
                  <p className="text-sm text-neutral-400 mt-0.5">{pos.description}</p>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {dataClerkReason && (
        <div className="mt-4 p-4 rounded-xl border-2 border-neutral-700 bg-neutral-900">
          <label className="flex items-center gap-3 cursor-pointer">
            <div
              className={`w-10 h-5 rounded-full transition-colors flex items-center ${
                enableDataClerk ? "bg-blue-600 justify-end" : "bg-neutral-600 justify-start"
              }`}
              onClick={() => setEnableDataClerk(!enableDataClerk)}
            >
              <div className="w-4 h-4 rounded-full bg-white mx-0.5" />
            </div>
            <div>
              <h3 className="font-semibold text-sm">启用数据研究员</h3>
              <p className="text-xs text-neutral-400 mt-0.5">{dataClerkReason}</p>
            </div>
          </label>
        </div>
      )}

      {preliminaryData && preliminaryData.length > 0 && (
        <details className="mt-4 p-4 rounded-xl border border-cyan-900/40 bg-cyan-950/20">
          <summary className="text-sm font-semibold text-cyan-400 cursor-pointer">
            研究数据预览（{preliminaryData.length} 条）
          </summary>
          <div className="mt-3 space-y-2">
            {preliminaryData.map((r, i) => (
              <div key={i} className="text-xs">
                <span className="text-cyan-400/70 font-medium">{r.title}</span>
                <span className="mx-1 text-neutral-600">-</span>
                <span className="text-neutral-400">
                  {r.snippet.slice(0, 150)}
                  {r.snippet.length > 150 ? "..." : ""}
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
        </details>
      )}

      <button
        onClick={handleStart}
        disabled={starting || selected.size < 2}
        className="mt-6 w-full bg-blue-600 hover:bg-blue-700 disabled:bg-neutral-700 disabled:text-neutral-500 text-white font-medium py-3 rounded-lg transition-colors"
      >
        {starting
          ? "正在启动..."
          : selected.size < 2
            ? `还需选择 ${2 - selected.size} 个立场`
            : "开始辩论"}
      </button>
    </div>
  );
}
