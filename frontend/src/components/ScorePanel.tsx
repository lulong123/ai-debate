import { useMemo } from "react";
import type { SSEEvent } from "../hooks/useSSE";

interface ScoreEntry {
  angleId: string;
  angleName: string;
  total: number;
  dimensions: { evidence: number; responsiveness: number; novelty: number };
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
          angle_id: string;
          angle_name: string;
          total: number;
          dimensions: { evidence: number; responsiveness: number; novelty: number };
          comment: string;
        }>;
        for (const s of rawScores) {
          scores.set(s.angle_id, {
            angleId: s.angle_id,
            angleName: s.angle_name,
            total: s.total,
            dimensions: s.dimensions,
            comment: s.comment,
            round,
          });
        }
      }
    }
    return Array.from(scores.values());
  }, [events]);

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
        实时评分
      </h3>
      {latestScores.map((score) => (
        <div key={score.angleId} className="bg-neutral-900 rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium">{score.angleName}</span>
            <span className="text-lg font-bold text-blue-400">{score.total}</span>
          </div>
          {/* Total score bar */}
          <div className="h-2 bg-neutral-800 rounded-full overflow-hidden mb-3">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-500"
              style={{ width: `${(score.total / 100) * 100}%` }}
            />
          </div>
          {/* Dimension scores */}
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <span className="text-neutral-500">论据</span>
              <div className="font-medium">{score.dimensions.evidence}</div>
            </div>
            <div>
              <span className="text-neutral-500">回应</span>
              <div className="font-medium">{score.dimensions.responsiveness}</div>
            </div>
            <div>
              <span className="text-neutral-500">新颖</span>
              <div className="font-medium">{score.dimensions.novelty}</div>
            </div>
          </div>
          {score.comment && (
            <p className="text-xs text-neutral-500 mt-2">{score.comment}</p>
          )}
        </div>
      ))}
    </div>
  );
}
