import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { FolderKanban, ListChecks, Rocket } from "lucide-react";
import { fetchCommands, fetchMeta, fetchProjects } from "./api.ts";
import { useStore } from "./store.ts";
import type { View } from "./store.ts";
import Launch from "./components/Launch.tsx";
import Jobs from "./components/Jobs.tsx";
import Projects from "./components/Projects.tsx";
import { cn } from "./components/ui.tsx";

const NAV: { id: View; label: string; icon: ReactNode }[] = [
  { id: "launch", label: "Launch", icon: <Rocket size={18} /> },
  { id: "jobs", label: "Jobs", icon: <ListChecks size={18} /> },
  { id: "projects", label: "Projects", icon: <FolderKanban size={18} /> },
];

const HEADER: Record<View, string> = {
  launch: "Launch a command",
  jobs: "Jobs",
  projects: "Projects",
};

export default function App(): ReactNode {
  const { view, setView, title, setTitle, setBackends, setCommands, setProjects } =
    useStore();
  const [connected, setConnected] = useState(true);

  // Load branding + backends once (they don't change during a session).
  useEffect(() => {
    let active = true;
    fetchMeta()
      .then((m) => {
        if (!active) return;
        setTitle(m.title);
        setBackends(m.backends);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [setTitle, setBackends]);

  // Load the command catalog. It does not change during a session, so a periodic
  // refetch would churn the store and disrupt a half-filled form — while
  // disconnected we retry on a timer; once connected we stop.
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const load = () =>
      fetchCommands()
        .then((c) => {
          if (!active) return;
          setCommands(c);
          setConnected(true);
          if (timer) window.clearInterval(timer);
        })
        .catch(() => active && setConnected(false));
    load();
    timer = window.setInterval(load, 5000);
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [setCommands]);

  // Keep the project list current (it changes when a project is created/deleted).
  useEffect(() => {
    let active = true;
    const load = () =>
      fetchProjects()
        .then((p) => active && setProjects(p))
        .catch(() => undefined);
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [setProjects]);

  const [brandLead, ...brandRest] = title.split(" ");

  return (
    <div className="flex min-h-screen">
      <aside className="w-52 shrink-0 border-r border-slate-800 bg-slate-950/60 p-4">
        <div className="mb-6 flex items-center gap-2">
          <span className="text-lg font-bold tracking-tight text-cyan-400">
            {brandLead}
          </span>
          {brandRest.length > 0 && (
            <span className="text-lg font-light text-slate-400">
              {brandRest.join(" ")}
            </span>
          )}
        </div>
        <nav className="space-y-1">
          {NAV.map((item) => (
            <button
              key={item.id}
              onClick={() => setView(item.id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium",
                view === item.id
                  ? "bg-cyan-950/50 text-cyan-300"
                  : "text-slate-400 hover:bg-slate-900 hover:text-slate-200",
              )}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="flex-1">
        <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
          <h1 className="text-sm font-semibold text-slate-300">{HEADER[view]}</h1>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span
              className={cn(
                "inline-block h-2 w-2 rounded-full",
                connected ? "bg-emerald-400" : "bg-red-500",
              )}
            />
            {connected ? "connected" : "disconnected"}
          </div>
        </header>
        <main className="p-6">
          {view === "launch" && <Launch />}
          {view === "jobs" && <Jobs />}
          {view === "projects" && <Projects />}
        </main>
      </div>
    </div>
  );
}
