import { useEffect, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { FolderKanban, FolderPlus, Search, Trash2 } from "lucide-react";
import { deleteProject, fetchHistory, fetchProjects } from "../api.ts";
import { useStore } from "../store.ts";
import type { History, JobRecord, Project } from "../types.ts";
import { NewProjectInput } from "./NewProjectInput.tsx";
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
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);

  // The history carries its own project list, so the poll only refetches that.
  // The global `store.projects` slice (which the Launch tab reads) is owned by
  // App.tsx's own poll; we refresh it eagerly only after a create/delete here.
  const reloadHistory = () => {
    void fetchHistory()
      .then(setHistory)
      .catch(() => undefined);
  };
  const refreshProjects = () => {
    void fetchProjects()
      .then(setProjects)
      .catch(() => undefined);
  };

  useEffect(() => {
    reloadHistory();
    const t = window.setInterval(reloadHistory, 3000);
    return () => window.clearInterval(t);
  }, []);

  const removeProject = (e: MouseEvent, project: Project, jobCount: number) => {
    e.stopPropagation();
    const detail =
      jobCount > 0
        ? `This permanently deletes the project AND its ${jobCount} ` +
          `job${jobCount === 1 ? "" : "s"} (their logs, configs, and output). ` +
          `This cannot be undone.`
        : "This cannot be undone.";
    if (window.confirm(`Delete project "${project.name}"?\n\n${detail}`)) {
      void deleteProject(project.id)
        .then(() => {
          reloadHistory();
          refreshProjects();
        })
        .catch(() => undefined);
    }
  };

  const needle = filter.trim().toLowerCase();
  const groups = (history?.projects ?? []).filter(
    (g) => !needle || g.project.name.toLowerCase().includes(needle),
  );

  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold text-slate-200">Projects</h1>
        <div className="relative">
          <Search
            size={14}
            className="pointer-events-none absolute left-2 top-2.5 text-slate-500"
          />
          <input
            className="w-48 rounded-md border border-slate-700 bg-slate-900 py-1.5 pl-7 pr-2 text-sm text-slate-100"
            placeholder="Filter projects…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="ml-auto">
          {creating ? (
            <NewProjectInput
              inputClassName="w-48"
              onCreated={() => {
                setCreating(false);
                reloadHistory();
                refreshProjects();
              }}
              onCancel={() => setCreating(false)}
            />
          ) : (
            <button
              type="button"
              className="rjsf-add-btn"
              onClick={() => setCreating(true)}
            >
              <FolderPlus size={14} /> New project
            </button>
          )}
        </div>
      </div>

      {groups.map(({ project, jobs }) => (
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
              title="Delete project (also deletes its jobs)"
              onClick={(e) => removeProject(e, project, jobs.length)}
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

      {!needle && history && history.ungrouped.length > 0 && (
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
        groups.length === 0 &&
        (needle || history.ungrouped.length === 0) && (
          <p className="text-slate-500">
            {needle
              ? "No projects match that filter."
              : "No jobs yet. Launch one, optionally filing it under a project."}
          </p>
        )}
    </div>
  );
}
