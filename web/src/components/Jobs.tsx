import { useEffect, useMemo, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Search, Trash2 } from "lucide-react";
import { deleteJob, fetchJobs } from "../api.ts";
import type { JobQuery } from "../types.ts";
import { useStore } from "../store.ts";
import type { JobRecord, JobStatus } from "../types.ts";
import JobDetail from "./JobDetail.tsx";
import { StatusChip, confirmDeleteJob, relativeTime } from "./ui.tsx";

const PAGE_SIZE = 25;
const STATUSES: JobStatus[] = ["queued", "running", "done", "failed", "cancelled"];
const SORTS: { key: string; label: string }[] = [
  { key: "created_at:desc", label: "Newest first" },
  { key: "created_at:asc", label: "Oldest first" },
  { key: "status:asc", label: "Status" },
  { key: "name:asc", label: "Name" },
];

const SELECT =
  "rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-200";

export default function Jobs(): ReactNode {
  const { selectedJobId, openJob, backends } = useStore();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<JobStatus | "">("");
  const [backend, setBackend] = useState("");
  const [sortKey, setSortKey] = useState("created_at:desc");
  const [page, setPage] = useState(0);

  const query = useMemo<JobQuery>(() => {
    const [sort, order] = sortKey.split(":");
    return {
      q: search || undefined,
      status: status || undefined,
      backend: backend || undefined,
      sort,
      order: order as "asc" | "desc",
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    };
  }, [search, status, backend, sortKey, page]);

  // Reset to the first page whenever a filter changes.
  useEffect(() => setPage(0), [search, status, backend, sortKey]);

  useEffect(() => {
    if (selectedJobId) return undefined; // detail view does its own polling
    let active = true;
    const poll = () =>
      fetchJobs(query)
        .then((p) => {
          if (!active) return;
          setJobs(p.jobs);
          setTotal(p.total);
        })
        .catch(() => undefined);
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [selectedJobId, query]);

  const removeJob = (e: MouseEvent, id: string) => {
    e.stopPropagation();
    if (confirmDeleteJob()) {
      setJobs((prev) => prev.filter((j) => j.id !== id));
      void deleteJob(id).catch(() => undefined);
    }
  };

  if (selectedJobId) return <JobDetail id={selectedJobId} />;

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search
            size={14}
            className="pointer-events-none absolute left-2 top-2.5 text-slate-500"
          />
          <input
            className="w-56 rounded-md border border-slate-700 bg-slate-900 py-1.5 pl-7 pr-2 text-sm text-slate-100"
            placeholder="Search name / command…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className={SELECT}
          value={status}
          onChange={(e) => setStatus(e.target.value as JobStatus | "")}
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className={SELECT}
          value={backend}
          onChange={(e) => setBackend(e.target.value)}
        >
          <option value="">All backends</option>
          {backends.map((b) => (
            <option key={b.key} value={b.key}>
              {b.key}
            </option>
          ))}
        </select>
        <select
          className={SELECT}
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value)}
        >
          {SORTS.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {jobs.length === 0 ? (
        <p className="text-slate-500">
          No jobs match. Launch one, or clear the filters.
        </p>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-slate-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/60 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Command</th>
                  <th className="px-4 py-2">Backend</th>
                  <th className="px-4 py-2">Started</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr
                    key={job.id}
                    onClick={() => openJob(job.id)}
                    className="cursor-pointer border-t border-slate-800 hover:bg-slate-900/40"
                  >
                    <td className="px-4 py-2">
                      <StatusChip status={job.status} />
                    </td>
                    <td className="px-4 py-2 text-slate-200">
                      {job.name || <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-4 py-2 text-slate-400">
                      {job.app}
                      <span className="text-slate-600"> / </span>
                      {job.command}
                    </td>
                    <td className="px-4 py-2 capitalize text-slate-400">
                      {job.backend}
                      {job.scheduler_id && (
                        <span className="ml-1 font-mono normal-case text-slate-600">
                          #{job.scheduler_id}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-400">
                      {relativeTime(job.created_at)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        aria-label="Delete job"
                        title="Delete job"
                        onClick={(e) => removeJob(e, job.id)}
                        className="text-slate-600 hover:text-red-400"
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
            <span>
              {from}–{to} of {total}
            </span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="rounded px-2 py-1 hover:text-slate-200 disabled:opacity-40"
              >
                ← Prev
              </button>
              <span>
                Page {page + 1} / {pages}
              </span>
              <button
                type="button"
                disabled={page + 1 >= pages}
                onClick={() => setPage((p) => p + 1)}
                className="rounded px-2 py-1 hover:text-slate-200 disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
