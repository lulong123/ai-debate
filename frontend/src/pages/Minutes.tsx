import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getMinutes } from "../lib/api";

interface MinutesData {
  core_conclusion: string;
  standpoints: Array<{
    angle: string;
    main_points: string[];
    position: string;
  }>;
  disagreements: string[];
  actionable_items: string[];
  summary: string;
  all_scores: Array<{
    angle_id: string;
    angle_name: string;
    total: number;
    dimensions: { evidence: number; responsiveness: number; novelty: number };
    comment: string;
  }>;
}

export function Minutes() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [minutes, setMinutes] = useState<MinutesData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!sessionId) return;
    getMinutes(sessionId)
      .then((data) => setMinutes(data.minutes))
      .catch(() => setMinutes(null))
      .finally(() => setLoading(false));
  }, [sessionId]);

  function exportMarkdown() {
    if (!minutes || !sessionId) return;
    const lines = [
      `# 会议纪要`,
      "",
      `## 核心结论`,
      minutes.core_conclusion,
      "",
      `## 各方立场`,
      ...minutes.standpoints.map(
        (s) => `### ${s.angle}\n立场：${s.position}\n\n要点：\n${s.main_points.map((p) => `- ${p}`).join("\n")}`
      ),
      "",
      `## 分歧点`,
      ...minutes.disagreements.map((d) => `- ${d}`),
      "",
      `## 可落地方案`,
      ...minutes.actionable_items.map((a) => `- ${a}`),
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
        <span className="ml-3 text-neutral-400">加载会议纪要...</span>
      </div>
    );
  }

  if (!minutes) {
    return (
      <div className="text-center py-20">
        <p className="text-neutral-400">暂无会议纪要</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">会议纪要</h2>
        <div className="flex gap-2">
          <button
            onClick={() => navigate(`/discussion/${sessionId}`)}
            className="text-sm text-neutral-400 hover:text-neutral-200 px-3 py-1.5 border border-neutral-800 rounded-lg"
          >
            查看讨论
          </button>
          <button
            onClick={exportMarkdown}
            className="text-sm bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-lg"
          >
            导出 Markdown
          </button>
        </div>
      </div>

      {/* Core conclusion */}
      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
        <h3 className="text-sm font-semibold text-blue-400 uppercase tracking-wider mb-2">
          核心结论
        </h3>
        <p className="text-lg">{minutes.core_conclusion}</p>
      </section>

      {/* Standpoints */}
      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
        <h3 className="text-sm font-semibold text-blue-400 uppercase tracking-wider mb-4">
          各方立场
        </h3>
        <div className="grid gap-4">
          {minutes.standpoints.map((s, i) => (
            <div key={i} className="border-l-2 border-neutral-700 pl-4">
              <h4 className="font-semibold mb-1">{s.angle}</h4>
              <p className="text-sm text-neutral-400 mb-2">{s.position}</p>
              <ul className="text-sm text-neutral-300 space-y-1">
                {s.main_points.map((p, j) => (
                  <li key={j} className="flex gap-2">
                    <span className="text-neutral-600">-</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* Disagreements */}
      {minutes.disagreements.length > 0 && (
        <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
          <h3 className="text-sm font-semibold text-amber-400 uppercase tracking-wider mb-2">
            分歧点
          </h3>
          <ul className="space-y-1">
            {minutes.disagreements.map((d, i) => (
              <li key={i} className="text-neutral-300 flex gap-2">
                <span className="text-amber-600">-</span>
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Actionable items */}
      {minutes.actionable_items.length > 0 && (
        <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-6 mb-4">
          <h3 className="text-sm font-semibold text-emerald-400 uppercase tracking-wider mb-2">
            可落地方案
          </h3>
          <ul className="space-y-1">
            {minutes.actionable_items.map((a, i) => (
              <li key={i} className="text-neutral-300 flex gap-2">
                <span className="text-emerald-600">-</span>
                <span>{a}</span>
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
