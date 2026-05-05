import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { suggestAngles, startDiscussion } from "../lib/api";

interface Angle {
  id: string;
  name: string;
  description: string;
}

export function Angles() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [angles, setAngles] = useState<Angle[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!sessionId) {
      setError("无效的讨论链接");
      setLoading(false);
      return;
    }
    suggestAngles(sessionId)
      .then((data) => {
        setAngles(data.angles || []);
      })
      .catch(() => {
        setError("获取角度建议失败，请检查网络连接");
      })
      .finally(() => setLoading(false));
  }, [sessionId]);

  const toggleAngle = useCallback((id: string) => {
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
      await startDiscussion(sessionId, Array.from(selected));
      navigate(`/discussion/${sessionId}`);
    } catch {
      setError("开始讨论失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full" />
        <span className="ml-3 text-neutral-400">主持人正在分析议题...</span>
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
        <h2 className="text-2xl font-bold mb-2">选择讨论角度</h2>
        <p className="text-neutral-400">至少选择 2 个角度开始讨论</p>
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
        {angles.map((angle, i) => {
          const isSelected = selected.has(angle.id);
          return (
            <button
              key={angle.id}
              onClick={() => toggleAngle(angle.id)}
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
                  <h3 className="font-semibold">{angle.name}</h3>
                  <p className="text-sm text-neutral-400 mt-0.5">{angle.description}</p>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <button
        onClick={handleStart}
        disabled={starting || selected.size < 2}
        className="mt-6 w-full bg-blue-600 hover:bg-blue-700 disabled:bg-neutral-700 disabled:text-neutral-500 text-white font-medium py-3 rounded-lg transition-colors"
      >
        {starting ? "正在启动..." : selected.size < 2 ? `还需选择 ${2 - selected.size} 个角度` : "开始讨论"}
      </button>
    </div>
  );
}
