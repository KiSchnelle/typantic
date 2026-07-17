import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowLeft, Check, Copy, Download, RotateCcw, WrapText } from "lucide-react";
import {
  cancelJob,
  deleteJob,
  fetchImages,
  fetchJob,
  fetchJobRequest,
  imageSrc,
  openLogSocket,
  restartJob,
  thumbSrc,
} from "../api.ts";
import { useStore } from "../store.ts";
import type { JobImage, JobRecord, LaunchRequest } from "../types.ts";
import { TERMINAL_STATUSES } from "../types.ts";
import { Button, StatusChip, cn, confirmDeleteJob, relativeTime } from "./ui.tsx";

// A job log is captured console output. If it was written with a Python logging
// handler that keeps the "LEVEL  time - name - message  file:line" layout (e.g.
// Rich without a TTY), we re-derive colour by parsing that structure. Lines that
// don't match render neutral, so plain stdout is fine too.
const LOG_LEVEL_RE = /^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b/;
// "message ... file.py:line" trailing source location (right-aligned).
const LOG_SRC_RE = /(\s+)([\w./-]+\.py:\d+)(\s*)$/;
// "  <timestamp> - <logger.name> - <message>" after the level keyword.
const LOG_META_RE = /^(\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - \S+ - )(.*)$/;

// Line colour per level; a record's wrapped continuation lines inherit its level.
const LEVEL_TEXT: Record<string, string> = {
  DEBUG: "text-slate-500",
  INFO: "text-slate-300",
  WARNING: "text-amber-300",
  ERROR: "text-red-300",
  CRITICAL: "text-red-300 font-semibold",
};
// The level keyword itself, a touch stronger than its line.
const LEVEL_TAG: Record<string, string> = {
  DEBUG: "text-slate-400",
  INFO: "text-cyan-400",
  WARNING: "text-amber-400 font-semibold",
  ERROR: "text-red-400 font-semibold",
  CRITICAL: "text-red-300 font-bold",
};

// Above this size, skip per-line spans and render plain text so a huge streaming
// log stays snappy (Copy/Download still give the full raw text either way).
const MAX_COLORIZE = 500_000;

function colorizeLog(log: string): ReactNode {
  if (log.length > MAX_COLORIZE) return log;
  const lines = log.split("\n");
  let level = "INFO";
  return lines.map((line, i) => {
    const nl = i < lines.length - 1 ? "\n" : "";
    const key = `${i}:${line}`;
    const header = LOG_LEVEL_RE.exec(line);
    if (header) {
      const tag = header[1];
      level = tag;
      let rest = line.slice(tag.length);
      let loc: ReactNode = null;
      const src = LOG_SRC_RE.exec(rest);
      if (src) {
        rest = rest.slice(0, src.index);
        loc = (
          <>
            {src[1]}
            <span className="text-slate-600">{src[2]}</span>
            {src[3]}
          </>
        );
      }
      const meta = LOG_META_RE.exec(rest);
      const body = meta ? (
        <>
          <span className="text-slate-500">{meta[1]}</span>
          {meta[2]}
        </>
      ) : (
        rest
      );
      return (
        <span key={key} className={LEVEL_TEXT[tag]}>
          <span className={LEVEL_TAG[tag]}>{tag}</span>
          {body}
          {loc}
          {nl}
        </span>
      );
    }
    if (!/^\s/.test(line)) level = "INFO";
    return (
      <span key={key} className={LEVEL_TEXT[level]}>
        {line}
        {nl}
      </span>
    );
  });
}

// One icon button in the log toolbar (wrap / copy / download).
function LogAction({
  onClick,
  title,
  active,
  children,
}: {
  onClick: () => void;
  title: string;
  active?: boolean;
  children: ReactNode;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cn(
        "rounded p-1 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200",
        active && "bg-slate-800 text-cyan-300",
      )}
    >
      {children}
    </button>
  );
}

// Each WS frame is a JSON envelope: {"log": "..."} for output, {"end": {...}} at
// the end. Wrapping the log means a log line can't be mistaken for the end frame.
function logChunk(data: string): string {
  try {
    const frame = JSON.parse(data) as { log?: unknown };
    return typeof frame.log === "string" ? frame.log : "";
  } catch {
    return "";
  }
}

// The backend options actually set on a job (unset fields are hidden so the panel
// shows only what was requested, not a wall of nulls/empties).
function usedOptions(opts: Record<string, unknown>): [string, unknown][] {
  return Object.entries(opts).filter(([, v]) =>
    Array.isArray(v)
      ? v.length > 0
      : v !== null && v !== undefined && v !== "",
  );
}

export default function JobDetail({ id }: { id: string }): ReactNode {
  const { closeJob, cloneFrom, editForRestart } = useStore();
  const [job, setJob] = useState<JobRecord | null>(null);
  const [request, setRequest] = useState<LaunchRequest | null>(null);
  const [log, setLog] = useState("");
  const [images, setImages] = useState<JobImage[]>([]);
  // Bumped on restart so the log-tail and image effects re-run even though the
  // job id is unchanged.
  const [runEpoch, setRunEpoch] = useState(0);
  const [showRestart, setShowRestart] = useState(false);
  const [wrap, setWrap] = useState(false);
  const [copied, setCopied] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  // Only auto-scroll the log when the user is already at the bottom.
  const stick = useRef(true);

  useEffect(() => {
    let active = true;
    const poll = () =>
      fetchJob(id)
        .then((j) => active && setJob(j))
        .catch(() => undefined);
    poll();
    const t = window.setInterval(poll, 2000);
    return () => {
      active = false;
      window.clearInterval(t);
    };
  }, [id]);

  useEffect(() => {
    let active = true;
    fetchJobRequest(id)
      .then((r) => active && setRequest(r))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [id]);

  useEffect(() => {
    setLog("");
    const ws = openLogSocket(id);
    ws.onmessage = (ev: MessageEvent<string>) => {
      const chunk = logChunk(ev.data);
      if (chunk) setLog((prev) => prev + chunk);
    };
    return () => ws.close();
  }, [id, runEpoch]);

  useEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [log]);

  const onLogScroll = () => {
    const el = logRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const copyLog = () => {
    void navigator.clipboard.writeText(log).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const downloadLog = () => {
    const blob = new Blob([log], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${job?.name || id}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    let active = true;
    setImages([]);
    const load = () =>
      fetchImages(id)
        .then((r) => active && setImages(r.images))
        .catch(() => undefined);
    load();
    const t = window.setInterval(load, 3000);
    return () => {
      active = false;
      window.clearInterval(t);
    };
  }, [id, runEpoch]);

  const terminal = job !== null && TERMINAL_STATUSES.includes(job.status);
  const options = request ? usedOptions(request.backend_options) : [];
  const logView = useMemo(() => (log ? colorizeLog(log) : null), [log]);

  return (
    <div>
      <button
        onClick={closeJob}
        className="mb-4 flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
      >
        <ArrowLeft size={16} /> All jobs
      </button>

      {job && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <StatusChip status={job.status} />
          <span className="text-lg font-semibold text-slate-100">
            {job.name || `${job.app} / ${job.command}`}
          </span>
          {job.name && (
            <span className="text-sm text-slate-500">
              {job.app} / {job.command}
            </span>
          )}
          <span className="text-sm text-slate-500 capitalize">{job.backend}</span>
          {job.scheduler_id && (
            <span className="font-mono text-sm text-slate-500">
              #{job.scheduler_id}
            </span>
          )}
          {job.pid !== null && (
            <span className="font-mono text-sm text-slate-500">pid {job.pid}</span>
          )}
          {job.exit_code !== null && (
            <span className="text-sm text-slate-500">exit {job.exit_code}</span>
          )}
          <span className="text-sm text-slate-500">
            started {relativeTime(job.created_at)}
          </span>
          <div className="ml-auto flex gap-2">
            <Button
              onClick={() => {
                void fetchJobRequest(id).then(cloneFrom);
              }}
            >
              Clone
            </Button>
            {terminal ? (
              <Button variant="danger" onClick={() => setShowRestart(true)}>
                Restart
              </Button>
            ) : (
              <Button
                variant="danger"
                onClick={() => {
                  void cancelJob(id).then(setJob);
                }}
              >
                Cancel
              </Button>
            )}
            <Button
              onClick={() => {
                if (confirmDeleteJob()) {
                  void deleteJob(id).then(closeJob);
                }
              }}
            >
              Delete
            </Button>
          </div>
        </div>
      )}

      {request && (
        <details className="mb-4 rounded-lg border border-slate-800 bg-slate-900/40">
          <summary className="cursor-pointer px-4 py-2 text-sm font-semibold text-slate-300">
            Config
          </summary>
          {options.length > 0 && (
            <div className="border-t border-slate-800 px-4 py-2 text-xs text-slate-400">
              <span className="text-slate-500">Backend options: </span>
              {options.map(([k, v]) => (
                <span key={k} className="mr-3 font-mono">
                  {k}={Array.isArray(v) ? v.join(",") : String(v)}
                </span>
              ))}
            </div>
          )}
          <pre className="mono max-h-[40vh] overflow-auto border-t border-slate-800 px-4 py-3 text-xs leading-relaxed text-slate-300">
            {JSON.stringify(request.values, null, 2)}
          </pre>
        </details>
      )}

      <div className="overflow-hidden rounded-lg border border-slate-800 bg-black/60">
        <div className="flex items-center gap-2 border-b border-slate-800 bg-slate-900/70 px-3 py-1.5">
          <span className="shrink-0 text-xs font-semibold text-slate-400">Log</span>
          {job && (
            <span
              className="min-w-0 truncate font-mono text-[11px] text-slate-600"
              title={job.log_path}
            >
              {job.log_path}
            </span>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-0.5">
            <LogAction
              onClick={() => setWrap((w) => !w)}
              title="Toggle line wrap"
              active={wrap}
            >
              <WrapText size={14} />
            </LogAction>
            <LogAction onClick={copyLog} title="Copy log">
              {copied ? (
                <Check size={14} className="text-emerald-400" />
              ) : (
                <Copy size={14} />
              )}
            </LogAction>
            <LogAction onClick={downloadLog} title="Download log">
              <Download size={14} />
            </LogAction>
          </div>
        </div>
        <pre
          ref={logRef}
          onScroll={onLogScroll}
          className={cn(
            "mono h-[60vh] overflow-auto px-4 py-3 text-xs leading-relaxed text-slate-300",
            "selection:bg-cyan-500/30",
            wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre",
          )}
        >
          {logView ?? (
            <span className="text-slate-600 italic">Waiting for output…</span>
          )}
        </pre>
      </div>

      {images.length > 0 && (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-semibold text-slate-300">
            Output images ({images.length})
          </h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {images.map((img) => (
              <a
                key={`${img.root}/${img.name}`}
                href={imageSrc(img.url)}
                target="_blank"
                rel="noreferrer"
                className="block overflow-hidden rounded-lg border border-slate-800 bg-black/30 [contain-intrinsic-size:auto_10rem] [content-visibility:auto] hover:border-slate-600"
              >
                <img
                  src={thumbSrc(img.url, 384)}
                  alt={img.name}
                  loading="lazy"
                  decoding="async"
                  className="h-32 w-full object-contain"
                />
                <div className="truncate px-2 py-1 font-mono text-[11px] text-slate-500">
                  {img.name}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}

      {showRestart && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setShowRestart(false)}
        >
          <div
            className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-slate-100">Restart job</h3>
            <p className="mt-1 text-sm text-slate-400">Re-run this job — pick how:</p>
            <div className="mt-4 flex flex-col gap-2">
              <button
                type="button"
                className="rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-left transition-colors hover:border-cyan-700 hover:bg-slate-800"
                onClick={() => {
                  setShowRestart(false);
                  void fetchJobRequest(id).then((req) => editForRestart(id, req));
                }}
              >
                <div className="flex items-center gap-2 font-medium text-slate-100">
                  <RotateCcw size={15} /> Change parameters…
                </div>
                <div className="mt-1 text-xs text-slate-400">
                  Open this job's settings to edit, then submit. Re-runs the same
                  job in place.
                </div>
              </button>
              <button
                type="button"
                className="rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-left transition-colors hover:border-red-700 hover:bg-slate-800"
                onClick={() => {
                  setShowRestart(false);
                  void restartJob(id).then((j) => {
                    setJob(j);
                    setRunEpoch((e) => e + 1);
                  });
                }}
              >
                <div className="font-medium text-red-200">Restart as-is</div>
                <div className="mt-1 text-xs text-slate-400">
                  Re-run with the same settings, in place.
                </div>
              </button>
            </div>
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                className="text-sm text-slate-500 hover:text-slate-300"
                onClick={() => setShowRestart(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
