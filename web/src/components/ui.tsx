import type { ReactNode } from "react";
import type { JobStatus } from "../types.ts";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

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
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
}): ReactNode {
  const styles = {
    default: "bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-600",
    primary: "bg-cyan-600 hover:bg-cyan-500 text-white border-cyan-500",
    danger: "bg-red-900/60 hover:bg-red-800 text-red-100 border-red-700",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
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
  active,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
}): ReactNode {
  return (
    <div
      onClick={onClick}
      className={cn(
        "rounded-lg border p-4 transition-colors",
        onClick && "cursor-pointer",
        active
          ? "border-cyan-600 bg-cyan-950/30"
          : "border-slate-800 bg-slate-900/40 hover:border-slate-700",
      )}
    >
      {children}
    </div>
  );
}

// Shared so the jobs-list bin icon and the detail Delete button warn identically.
// Delete is destructive — it removes the job and its output folder — so the
// prompt spells that out and asks for an explicit confirmation.
export function confirmDeleteJob(): boolean {
  return window.confirm(
    "Really delete this job?\n\n" +
      "This permanently removes the job (its log and config) AND its output " +
      "folder — every file it produced.\n\n" +
      "This cannot be undone.",
  );
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
