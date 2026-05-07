import { useMemo } from "react";
import type { SSEEvent } from "../hooks/useSSE";

interface ScoreEntry {
  positionId: string;
  positionName: string;
  points: number;
  comment: string;
  round: number;
}

interface ScorePanelProps {
  events: SSEEvent[];
}

export function ScorePanel({ events }: ScorePanelProps) {
  const latestScores = useMemo(() => {
    const scores: Map<string, ScoreEntry> = new Map();
    for (const event of events) {
      if (event.type === "score_update") {
        const round = event.round as number;
        const rawScores = event.scores as Array<{
          position_id: string;
          position_name: string;
          points: number;
          comment: string;
        }>;
        for (const s of rawScores) {
          scores.set(s.position_id, {
            positionId: s.position_id,
            positionName: s.position_name,
            points: s.points,
            comment: s.comment,
            round,
          });
        }
      }
    }
    return Array.from(scores.values());
  }, [events]);

  const totalPoints = latestScores.reduce((sum, s) => sum + s.points, 0);

  if (latestScores.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-neutral-500 text-sm">
        等待评分...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider">
        实时评分（总分 100）
      </h3>
      {latestScores.map((score) => {
        const pct = totalPoints > 0 ? Math.round((score.points / totalPoints) * 100) : 0;
        return (
          <div key={score.positionId} className="bg-neutral-900 rounded-lg p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">{score.positionName}</span>
              <span className="text-lg font-bold text-blue-400">{score.points}</span>
            </div>
            <div className="h-2 bg-neutral-800 rounded-full overflow-hidden mb-2">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="text-xs text-neutral-500">{pct}%</div>
            {score.comment && (
              <p className="text-xs text-neutral-500 mt-2">{score.comment}</p>
            )}
          </div>
        );
      })}
      <div className="text-xs text-neutral-600 text-center">
        分数之和：{totalPoints}
      </div>
    </div>
  );
}
