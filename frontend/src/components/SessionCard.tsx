import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteSession, updateSession, type SessionListItem } from "../lib/api";

interface SessionCardProps {
  session: SessionListItem;
  onDeleted: () => void;
  onRenamed: (id: string, topic: string) => void;
}

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  CLARIFYING: { label: "审议中", color: "text-yellow-400", bg: "bg-yellow-500" },
  SELECTING_POSITIONS: { label: "选角度", color: "text-purple-400", bg: "bg-purple-500" },
  DISCUSSING: { label: "辩论中", color: "text-green-400", bg: "bg-green-500" },
  COMPLETED: { label: "已结束", color: "text-blue-400", bg: "bg-blue-500" },
  FAILED: { label: "出错", color: "text-red-400", bg: "bg-red-500" },
};

function formatTimeAgo(dateStr: string | null): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}小时前`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 30) return `${diffD}天前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export function SessionCard({ session, onDeleted, onRenamed }: SessionCardProps) {
  const navigate = useNavigate();
  const [showMenu, setShowMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(session.topic);
  const [deleting, setDeleting] = useState(false);

  const statusCfg = STATUS_CONFIG[session.status] ?? { label: session.status, color: "text-neutral-400", bg: "bg-neutral-500" };

  function handleClick() {
    if (renaming) return;
    if (session.status === "COMPLETED") {
      navigate(`/discussion/${session.session_id}`);
    } else if (session.status === "DISCUSSING") {
      navigate(`/discussion/${session.session_id}`);
    } else if (session.status === "SELECTING_POSITIONS") {
      navigate(`/positions/${session.session_id}`);
    } else {
      navigate(`/discussion/${session.session_id}`);
    }
  }

  async function handleDelete() {
    if (deleting) return;
    setDeleting(true);
    try {
      await deleteSession(session.session_id);
      onDeleted();
    } catch {
      // ignore
    }
  }

  async function handleRename() {
    if (!renameValue.trim() || renameValue.trim() === session.topic) {
      setRenaming(false);
      return;
    }
    try {
      await updateSession(session.session_id, renameValue.trim());
      onRenamed(session.session_id, renameValue.trim());
    } catch {
      // ignore
    }
    setRenaming(false);
  }

  return (
    <div
      onClick={handleClick}
      className="group relative bg-neutral-900 border border-neutral-800 rounded-lg p-4 cursor-pointer hover:border-neutral-700 transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {renaming ? (
            <input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onBlur={handleRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleRename();
                if (e.key === "Escape") setRenaming(false);
              }}
              onClick={(e) => e.stopPropagation()}
              autoFocus
              className="w-full bg-neutral-800 border border-neutral-600 rounded px-2 py-1 text-sm text-neutral-100 focus:outline-none focus:border-blue-500"
            />
          ) : (
            <h3 className="text-sm font-medium text-neutral-200 truncate">
              {session.topic}
            </h3>
          )}
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <span className={`inline-flex items-center gap-1 text-xs ${statusCfg.color}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${statusCfg.bg}`} />
              {statusCfg.label}
            </span>
            {session.winner && (
              <span className="text-xs text-neutral-500">
                胜方: {session.winner}
              </span>
            )}
            {session.current_round > 0 && (
              <span className="text-xs text-neutral-600">
                {session.current_round}/{session.max_rounds} 轮
              </span>
            )}
            <span className="text-xs text-neutral-600">
              {formatTimeAgo(session.created_at)}
            </span>
          </div>
        </div>

        {/* Menu */}
        <div className="relative" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => setShowMenu(!showMenu)}
            className="p-1 text-neutral-600 hover:text-neutral-300 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 5v.01M12 12v.01M12 19v.01" />
            </svg>
          </button>
          {showMenu && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setShowMenu(false)} />
              <div className="absolute right-0 top-6 z-20 bg-neutral-800 border border-neutral-700 rounded-lg shadow-xl py-1 min-w-[100px]">
                <button
                  onClick={() => { setShowMenu(false); setRenaming(true); }}
                  className="w-full text-left px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-700"
                >
                  重命名
                </button>
                <button
                  onClick={() => { setShowMenu(false); handleDelete(); }}
                  disabled={deleting}
                  className="w-full text-left px-3 py-1.5 text-sm text-red-400 hover:bg-neutral-700"
                >
                  {deleting ? "删除中..." : "删除"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
