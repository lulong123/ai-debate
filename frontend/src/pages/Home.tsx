import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSession, clarifyTopic, refineTopic } from "../lib/api";

export function Home() {
  const navigate = useNavigate();
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clarification, setClarification] = useState<{
    question: string;
    sessionId: string;
  } | null>(null);

  async function handleSubmit() {
    if (!topic.trim()) return;
    setLoading(true);
    setError("");
    try {
      const { session_id } = await createSession(topic.trim());
      const result = await clarifyTopic(session_id);
      if (result.valid) {
        navigate(`/angles/${session_id}`);
      } else {
        setClarification({ question: result.question, sessionId: session_id });
      }
    } catch {
      setError("创建失败，请检查网络连接后重试");
    } finally {
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
      navigate(`/angles/${clarification.sessionId}`);
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
        <h2 className="text-3xl font-bold mb-2">开始一场讨论</h2>
        <p className="text-neutral-400">
          提交一个议题，让多个 AI 角度协作分析，帮你做出更好的判断
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
          placeholder="输入你想讨论的议题...&#10;&#10;例如：该不该禁止 AI 生成深度伪造内容？"
          className="w-full h-32 bg-neutral-800 border border-neutral-700 rounded-lg p-4 text-neutral-100 placeholder-neutral-500 resize-none focus:outline-none focus:border-blue-500"
          disabled={loading}
        />
        <button
          onClick={handleSubmit}
          disabled={loading || !topic.trim()}
          className="mt-4 w-full bg-blue-600 hover:bg-blue-700 disabled:bg-neutral-700 disabled:text-neutral-500 text-white font-medium py-3 rounded-lg transition-colors"
        >
          {loading ? "分析中..." : "提交议题"}
        </button>
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
