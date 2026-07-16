import { useEffect, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Trash2 } from "lucide-react";
import { deleteJob, fetchJobs } from "../api.ts";
import { useStore } from "../store.ts";
import type { JobRecord } from "../types.ts";
import JobDetail from "./JobDetail.tsx";
import { StatusChip, confirmDeleteJob, relativeTime } from "./ui.tsx";

export default function Jobs(): ReactNode {
  const { selectedJobId, openJob } = useStore();
  const [jobs, setJobs] = useState<JobRecord[]>([]);

  useEffect(() => {
    if (selectedJobId) return undefined; // detail view does its own polling
    let active = true;
    const poll = () =>
      fetchJobs()
        .then((j) => active && setJobs(j))
        .catch(() => undefined);
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [selectedJobId]);

  const removeJob = (e: MouseEvent, id: string) => {
    e.stopPropagation(); // don't open the job detail on the row click
    if (confirmDeleteJob()) {
      // Drop it optimistically; the next poll reconciles if the server disagrees.
      setJobs((prev) => prev.filter((j) => j.id !== id));
      void deleteJob(id).catch(() => undefined);
    }
  };

  if (selectedJobId) return <JobDetail id={selectedJobId} />;

  if (jobs.length === 0) {
    return <p className="text-slate-500">No jobs yet. Launch one to get started.</p>;
  }

  return (
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
  );
}
