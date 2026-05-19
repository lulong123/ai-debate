import { useState } from "react";
import { useSSE } from "../hooks/useSSE";
import JRPGDialogue from "../components/JRPGDialogue";
import ReviewMode from "../components/ReviewMode";

interface DiscussionProps {
  sessionId: string;
}

export default function Discussion({ sessionId }: DiscussionProps) {
  const { events, connected } = useSSE(sessionId);
  const [mode, setMode] = useState<"jrpg" | "review">("jrpg");

  return (
    <div className="relative w-full h-screen flex flex-col">
      {/* Main content - both modes rendered but only one visible */}
      <div className="flex-1 relative">
        <div className={`absolute inset-0 ${mode === "jrpg" ? "" : "hidden"}`}>
          <JRPGDialogue events={events} connected={connected} />
        </div>
        <div className={`absolute inset-0 flex flex-col bg-neutral-950 ${mode === "review" ? "" : "hidden"}`}>
          <ReviewMode events={events} />
        </div>
      </div>

      {/* Bottom mode switch bar */}
      <div className="relative z-30 flex items-center justify-center gap-2 px-4 py-3 bg-neutral-900/95 border-t border-neutral-800 backdrop-blur-sm">
        <button
          onClick={() => setMode("jrpg")}
          className={`px-5 py-2 rounded-xl text-sm font-medium transition-all ${
            mode === "jrpg"
              ? "bg-amber-500/20 text-amber-400 border border-amber-500/40"
              : "text-neutral-500 hover:text-neutral-300 border border-transparent hover:border-neutral-700"
          }`}
        >
          ⚔ JRPG 对话
        </button>
        <button
          onClick={() => setMode("review")}
          className={`px-5 py-2 rounded-xl text-sm font-medium transition-all ${
            mode === "review"
              ? "bg-blue-500/20 text-blue-400 border border-blue-500/40"
              : "text-neutral-500 hover:text-neutral-300 border border-transparent hover:border-neutral-700"
          }`}
        >
          📜 回看模式
        </button>
      </div>
    </div>
  );
}
