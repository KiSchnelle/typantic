import { useEffect, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { FolderKanban, FolderPlus, Trash2 } from "lucide-react";
import {
  createProject,
  deleteProject,
  fetchHistory,
  fetchProjects,
} from "../api.ts";
import { useStore } from "../store.ts";
import type { History, JobRecord } from "../types.ts";
import { StatusChip, relativeTime } from "./ui.tsx";

function JobRow({ job }: { job: JobRecord }): ReactNode {
  const openJob = useStore((s) => s.openJob);
  return (
    <button
      type="button"
      onClick={() => openJob(job.id)}
      className="flex w-full items-center gap-3 border-t border-slate-800 px-4 py-2 text-left text-sm hover:bg-slate-900/40"
    >
      <StatusChip status={job.status} />
      <span className="text-slate-200">
        {job.name || <span className="text-slate-500">—</span>}
      </span>
      <span className="text-slate-500">
        {job.app} / {job.command}
      </span>
      <span className="ml-auto text-slate-500">{relativeTime(job.created_at)}</span>
    </button>
  );
}

export default function Projects(): ReactNode {
  const setProjects = useStore((s) => s.setProjects);
  const [history, setHistory] = useState<History | null>(null);

  const reload = () => {
    void fetchHistory()
      .then(setHistory)
      .catch(() => undefined);
    void fetchProjects()
      .then(setProjects)
      .catch(() => undefined);
  };

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 3000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newProject = () => {
    const name = window.prompt("New project name:");
    if (!name || !name.trim()) return;
    void createProject(name.trim()).then(reload).catch(() => undefined);
  };

  const removeProject = (e: MouseEvent, id: string) => {
    e.stopPropagation();
    if (
      window.confirm(
        "Delete this project? Its jobs are kept (moved to ungrouped), not deleted.",
      )
    ) {
      void deleteProject(id).then(reload).catch(() => undefined);
    }
  };

  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-200">Projects</h1>
        <button
          type="button"
          className="rjsf-add-btn"
          onClick={newProject}
        >
          <FolderPlus size={14} /> New project
        </button>
      </div>

      {history?.projects.map(({ project, jobs }) => (
        <div
          key={project.id}
          className="overflow-hidden rounded-lg border border-slate-800"
        >
          <div className="flex items-center gap-2 bg-slate-900/60 px-4 py-2">
            <FolderKanban size={16} className="text-cyan-400" />
            <span className="font-semibold text-slate-200">{project.name}</span>
            {project.description && (
              <span className="text-sm text-slate-500">{project.description}</span>
            )}
            <span className="ml-auto text-xs text-slate-500">
              {jobs.length} job{jobs.length === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              aria-label="Delete project"
              title="Delete project"
              onClick={(e) => removeProject(e, project.id)}
              className="text-slate-600 hover:text-red-400"
            >
              <Trash2 size={15} />
            </button>
          </div>
          {jobs.length === 0 ? (
            <div className="border-t border-slate-800 px-4 py-3 text-sm text-slate-600">
              No jobs in this project yet.
            </div>
          ) : (
            jobs.map((job) => <JobRow key={job.id} job={job} />)
          )}
        </div>
      ))}

      {history && history.ungrouped.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <div className="bg-slate-900/60 px-4 py-2 font-semibold text-slate-300">
            Ungrouped jobs
          </div>
          {history.ungrouped.map((job) => (
            <JobRow key={job.id} job={job} />
          ))}
        </div>
      )}

      {history &&
        history.projects.length === 0 &&
        history.ungrouped.length === 0 && (
          <p className="text-slate-500">
            No jobs yet. Launch one, optionally filing it under a project.
          </p>
        )}
    </div>
  );
}
