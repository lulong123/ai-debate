import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getMinutes } from "../lib/api";

interface MinutesData {
  core_conclusion: string;
  position_arguments: Array<{
    position: string;
    main_points: string[];
    defense: string;
  }>;
  key_clashes: string[];
  verdict: {
    winner: string;
    rationale: string;
    score_summary: string;
  };
  summary: string;
}

export function Minutes() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [minutes, setMinutes] = useState<MinutesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    setFetchError(false);
    getMinutes(sessionId)
      .then((data) => setMinutes(data.minutes))
      .catch(() => {
        setMinutes(null);
        setFetchError(true);
      })
      .finally(() => setLoading(false));
  }, [sessionId]);

  function exportMarkdown() {
    if (!minutes || !sessionId) return;
    const lines = [
      `# 辩论裁决`,
      "",
      `## 核心结论`,
      minutes.core_conclusion,
      "",
      `## 各方论证`,
      ...minutes.position_arguments.map(
        (p) => `### ${p.position}\n辩护：${p.defense}\n\n要点：\n${p.main_points.map((pt) => `- ${pt}`).join("\n")}`
      ),
      "",
      `## 关键冲突`,
      ...minutes.key_clashes.map((c) => `- ${c}`),
      "",
      `## 裁决`,
      minutes.verdict.winner ? `获胜方：**${minutes.verdict.winner}**` : "平局",
      `理由：${minutes.verdict.rationale}`,
      `评分：${minutes.verdict.score_summary}`,
      "",
      `## 总结`,
      minutes.summary,
    ];
    const md = lines.join("\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `roundtable-${sessionId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full" />
        <span className="ml-3 text-neutral-400">加载辩论裁决...</span>
      </div>
    );
  }

  if (!minutes) {
    return (
      <div className="text-center py-20">
        <p className="text-neutral-400">
          {fetchError ? "加载失败，请检查网络连接" : "暂无裁决"}
        </p>
        <button
          onClick={() => fetchError ? window.location.reload() : navigate("/")}
          className="text-sm text-blue-400 hover:text-blue-300 mt-3"
        >
          {fetchError ? "重试" : "返回首页"}
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">辩论裁决</h2>
        <div className="flex gap-2">
          <button
            onClick={() => navigate(`/discussion/${sessionId}`)}
            className="text-sm text-neutral-400 hover:text-neutral-200 px-3 py-1.5 border border-neutral-800 rounded-lg"
          >
            查看辩论
          </button>
          <button
            onClick={exportMarkdown}
            className="text-sm bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-lg"
          >
            导出 Markdown
          </button>
        </div>
      </div>

      {/* Verdict */}
      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
        <h3 className="text-sm font-semibold text-emerald-400 uppercase tracking-wider mb-2">
          裁决
        </h3>
        {minutes.verdict.winner ? (
          <p className="text-xl font-bold text-emerald-400 mb-2">
            获胜方：{minutes.verdict.winner}
          </p>
        ) : (
          <p className="text-xl font-bold text-neutral-400 mb-2">平局</p>
        )}
        <p className="text-neutral-300">{minutes.verdict.rationale}</p>
        {minutes.verdict.score_summary && (
          <p className="text-sm text-neutral-500 mt-2">评分：{minutes.verdict.score_summary}</p>
        )}
      </section>

      {/* Core conclusion */}
      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
        <h3 className="text-sm font-semibold text-blue-400 uppercase tracking-wider mb-2">
          核心结论
        </h3>
        <p className="text-lg">{minutes.core_conclusion}</p>
      </section>

      {/* Position arguments */}
      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
        <h3 className="text-sm font-semibold text-blue-400 uppercase tracking-wider mb-4">
          各方论证
        </h3>
        <div className="grid gap-4">
          {minutes.position_arguments.map((p, i) => (
            <div key={i} className="border-l-2 border-neutral-700 pl-4">
              <h4 className="font-semibold mb-1">{p.position}</h4>
              <p className="text-sm text-neutral-400 mb-2">{p.defense}</p>
              <ul className="text-sm text-neutral-300 space-y-1">
                {p.main_points.map((pt, j) => (
                  <li key={j} className="flex gap-2">
                    <span className="text-neutral-600">-</span>
                    <span>{pt}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* Key clashes */}
      {minutes.key_clashes.length > 0 && (
        <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
          <h3 className="text-sm font-semibold text-amber-400 uppercase tracking-wider mb-2">
            关键冲突
          </h3>
          <ul className="space-y-1">
            {minutes.key_clashes.map((c, i) => (
              <li key={i} className="text-neutral-300 flex gap-2">
                <span className="text-amber-600">-</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Summary */}
      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6">
        <h3 className="text-sm font-semibold text-blue-400 uppercase tracking-wider mb-2">
          总结
        </h3>
        <p className="text-neutral-300 leading-relaxed">{minutes.summary}</p>
      </section>
    </div>
  );
}
