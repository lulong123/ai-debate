import { Outlet } from "react-router-dom";

export function App() {
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <header className="border-b border-neutral-800 px-6 py-4">
        <h1 className="text-xl font-bold tracking-tight">
          AI 圆桌会议
        </h1>
        <p className="text-sm text-neutral-500 mt-0.5">
          多角度协作讨论，帮你做出更好的判断
        </p>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
