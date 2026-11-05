import type { ReactNode } from "react";
import { fetchJobRemoval, fetchProjectRemoval } from "../api.ts";
import type { JobRemoval, JobStatus, Project } from "../types.ts";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// Status colours are semantic, beside red "failed": they stay put whatever
// the brand's accent, so "running" is Tailwind's cyan, not brand-*.
const STATUS_STYLE: Record<JobStatus, string> = {
  queued: "bg-slate-700/40 text-slate-300 border-slate-600",
  running: "bg-cyan-500/15 text-cyan-300 border-cyan-700",
  done: "bg-emerald-500/15 text-emerald-300 border-emerald-700",
  failed: "bg-red-500/15 text-red-300 border-red-700",
  cancelled: "bg-amber-500/15 text-amber-300 border-amber-700",
};

export function StatusChip({ status }: { status: JobStatus }): ReactNode {
  return (
    <span
      className={cn(
        "inline-block rounded-full border px-2 py-0.5 text-xs font-semibold capitalize",
        STATUS_STYLE[status],
      )}
    >
      {status}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  title,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger";
  disabled?: boolean;
  // A tooltip, e.g. why the button is disabled.
  title?: string;
  type?: "button" | "submit";
}): ReactNode {
  const styles = {
    default: "bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-600",
    primary: "bg-brand-600 hover:bg-brand-500 text-white border-brand-500",
    danger: "bg-red-900/60 hover:bg-red-800 text-red-100 border-red-700",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        styles,
      )}
    >
      {children}
    </button>
  );
}

export function Card({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick?: () => void;
}): ReactNode {
  return (
    <div
      onClick={onClick}
      className={cn(
        "rounded-lg border border-slate-800 bg-slate-900/40 p-4 transition-colors",
        "hover:border-slate-700",
        onClick && "cursor-pointer",
      )}
    >
      {children}
    </div>
  );
}

// A failed action's message, without JavaScript's "Error: " prefix.
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// A failed action, shown where it was taken instead of failing silently.
export function Alert({ children }: { children: ReactNode }): ReactNode {
  return (
    <div
      role="alert"
      className="mb-4 rounded-lg border border-red-900 bg-red-950/40 px-4 py-2 text-sm text-red-200"
    >
      {children}
    </div>
  );
}

// Browsers cut a long confirm() short (Chromium near 2000 characters or 32 rows),
// and a folder the dialog did not show must not go. So the dialog stays under that,
// and what does not fit is not confirmed: those folders stay on disk.
const MAX_GOING_SHOWN = 15;
const MAX_GOING_CHARS = 1200;
const MAX_KEPT_SHOWN = 5;
const MAX_KEPT_CHARS = 400;

function fitting(lines: string[], maxLines: number, maxChars: number): string[] {
  const shown: string[] = [];
  let chars = 0;
  for (const line of lines) {
    chars += line.length + 1;
    if (shown.length === maxLines || chars > maxChars) break;
    shown.push(line);
  }
  return shown;
}

function outputLines(going: string[], left: number, removals: JobRemoval[]): string {
  const kept = [
    ...new Set(
      removals
        .filter((r) => r.kept_reason)
        .map((r) => `Keeps output folder ${r.output_folder}: ${r.kept_reason}`),
    ),
  ];
  const shown = fitting(kept, MAX_KEPT_SHOWN, MAX_KEPT_CHARS);
  if (kept.length > shown.length) shown.push(`…and ${kept.length - shown.length} more kept`);
  if (left > 0) {
    shown.push(
      `${left} more output folder${left === 1 ? "" : "s"} stay on disk: ` +
        "too many to list here. Delete those by hand.",
    );
  }
  const parts = [
    going.length > 0
      ? `Deletes ${going.length === 1 ? "this output folder" : "these output folders"} ` +
        `and everything in ${going.length === 1 ? "it" : "them"}:\n${going.join("\n")}`
      : "",
    shown.join("\n"),
  ];
  return parts
    .filter(Boolean)
    .map((part) => `${part}\n\n`)
    .join("");
}

// The question goes over the network first, so a double click would otherwise ask
// twice. Per item, so a request that hangs blocks only that item's delete.
const asking = new Set<string>();

// Ask before deleting, naming the exact output folders that go. The answer is those
// folders for the delete to send, or null to delete nothing. If the server cannot
// say what would go, this rejects and nothing is deleted.
async function confirmRemoval(
  key: string,
  load: () => Promise<JobRemoval[]>,
  message: (removals: JobRemoval[], output: string) => string,
): Promise<string[] | null> {
  if (asking.has(key)) return null;
  asking.add(key);
  try {
    const removals = await load();
    const all = [
      ...new Set(
        removals.flatMap((r) =>
          r.removes_output && r.output_folder !== null ? [r.output_folder] : [],
        ),
      ),
    ];
    const going = fitting(all, MAX_GOING_SHOWN, MAX_GOING_CHARS);
    const output = outputLines(going, all.length - going.length, removals);
    return window.confirm(message(removals, output)) ? going : null;
  } finally {
    asking.delete(key);
  }
}

// Shared so the jobs-list bin icon and the detail Delete button warn identically.
export function confirmDeleteJob(id: string): Promise<string[] | null> {
  return confirmRemoval(
    `job:${id}`,
    () => fetchJobRemoval(id).then((removal) => [removal]),
    (_, output) =>
      "Really delete this job?\n\n" +
      "This permanently removes the job and everything in its folder (log, " +
      "config, any files it wrote there), and cancels it if it is running.\n\n" +
      output +
      "This cannot be undone.",
  );
}

export function confirmDeleteProject(project: Project): Promise<string[] | null> {
  return confirmRemoval(
    `project:${project.id}`,
    () => fetchProjectRemoval(project.id),
    (removals, output) =>
      `Delete project "${project.name}"?\n\n` +
      (removals.length > 0
        ? `This permanently deletes the project AND its ${removals.length} ` +
          `job${removals.length === 1 ? "" : "s"}, with everything in their folders, ` +
          "cancelling any still running.\n\n"
        : "") +
      output +
      "This cannot be undone.",
  );
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  // A server clock a little ahead of this one puts a fresh time in the future.
  if (diff < 1) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
