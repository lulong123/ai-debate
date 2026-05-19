import { useMemo, useState } from "react";

export interface DataPoolEntry {
  title: string;
  snippet: string;
  url: string;
}

/** Parse [N] citations from text and render as badges with tooltips */
export function CitationText({ text, poolMap }: { text: string; poolMap: Map<number, DataPoolEntry> }) {
  const [activeTooltip, setActiveTooltip] = useState<number | null>(null);

  const parts = useMemo(() => {
    const result: Array<{ type: "text" | "cite"; value: string; num: number }> = [];
    const regex = /\[(\d+)\]/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        result.push({ type: "text", value: text.slice(lastIndex, match.index), num: 0 });
      }
      result.push({ type: "cite", value: match[0], num: parseInt(match[1], 10) });
      lastIndex = regex.lastIndex;
    }
    if (lastIndex < text.length) {
      result.push({ type: "text", value: text.slice(lastIndex), num: 0 });
    }
    return result;
  }, [text]);

  if (parts.length === 0 || (parts.length === 1 && parts[0].type === "text")) {
    return <>{text}</>;
  }

  return (
    <>
      {parts.map((part, i) => {
        if (part.type === "text") return <span key={i}>{part.value}</span>;

        const entry = poolMap.get(part.num);
        return (
          <span key={i} className="relative inline-block">
            <button
              className="inline-flex items-center justify-center
                w-4 h-4 text-[10px] font-bold leading-none
                bg-blue-600/80 hover:bg-blue-500 text-white
                rounded-full align-super ml-0.5 mr-0.5
                cursor-pointer transition-colors"
              onClick={(e) => {
                e.stopPropagation();
                setActiveTooltip(activeTooltip === part.num ? null : part.num);
              }}
              title={entry ? `${entry.title}: ${entry.snippet.slice(0, 100)}` : `数据池 [${part.num}]`}
            >
              {part.num}
            </button>
            {activeTooltip === part.num && entry && (
              <div
                className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2
                  w-64 p-3 rounded-lg bg-neutral-800 border border-neutral-700 shadow-xl
                  text-left"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="text-xs font-semibold text-blue-400 mb-1">{entry.title}</div>
                <p className="text-xs text-neutral-300 leading-relaxed mb-1.5">
                  {entry.snippet.slice(0, 200)}{entry.snippet.length > 200 ? "..." : ""}
                </p>
                {entry.url && (
                  <a
                    href={entry.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-cyan-400 hover:text-cyan-300 truncate block max-w-full"
                  >
                    {entry.url}
                  </a>
                )}
                <button
                  className="absolute top-1 right-1.5 text-neutral-500 hover:text-neutral-300 text-xs"
                  onClick={() => setActiveTooltip(null)}
                >
                  ✕
                </button>
              </div>
            )}
          </span>
        );
      })}
    </>
  );
}
