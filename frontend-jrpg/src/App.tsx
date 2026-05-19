import { useState } from "react";
import Discussion from "./pages/Discussion.tsx";

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      {sessionId ? (
        <Discussion sessionId={sessionId} />
      ) : (
        <Home onSessionCreated={setSessionId} />
      )}
    </div>
  );
}

/** 简易首页：输入议题，创建 session */
function Home({ onSessionCreated }: { onSessionCreated: (id: string) => void }) {
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: topic.trim(), max_rounds: 3 }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { session_id: string };
      onSessionCreated(data.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen p-4">
      <div className="w-full max-w-lg space-y-6">
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-amber-400 to-violet-400 bg-clip-text text-transparent">
            ⚔ 辩论场
          </h1>
          <p className="text-neutral-400 text-sm">JRPG 对话模式 · AI 辩论模拟</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-neutral-300 mb-1.5">
              辩论议题
            </label>
            <textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="请输入你想要辩论的议题..."
              className="w-full h-32 px-4 py-3 rounded-xl bg-neutral-900 border border-neutral-700
                text-neutral-100 placeholder-neutral-500 resize-none
                focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:border-amber-500
                transition"
            />
          </div>

          {error && (
            <div className="text-red-400 text-sm bg-red-400/10 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !topic.trim()}
            className="w-full py-3 rounded-xl font-semibold text-black
              bg-gradient-to-r from-amber-400 to-amber-500
              hover:from-amber-300 hover:to-amber-400
              disabled:opacity-40 disabled:cursor-not-allowed
              transition-all"
          >
            {loading ? "创建中..." : "开始辩论"}
          </button>
        </form>

        <p className="text-center text-xs text-neutral-600">
          输入议题后系统将自动分配辩手角色
        </p>
      </div>
    </div>
  );
}
