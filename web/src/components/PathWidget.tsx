import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { WidgetProps } from "@rjsf/utils";
import {
  ArrowUp,
  File as FileIcon,
  Folder,
  FolderOpen,
  FolderPlus,
  RefreshCw,
  X,
} from "lucide-react";
import { createDir, fetchDir } from "../api.ts";
import type { FsEntry, FsListing } from "../types.ts";

type PickerMode = "file" | "dir" | "any";

// Fixed row height (must match .dir-row in index.css) so the list can be
// windowed: only the rows in view are in the DOM, which keeps 100k-entry
// directories smooth.
const ROW_H = 34;
const OVERSCAN = 6;
const LAST_DIR_KEY = "typantic-web:last-dir";

// Process-lifetime cache of listings by directory path. Revisiting a directory
// (going up, back, or reopening the picker) is then instant instead of
// re-listing the folder. The refresh button bypasses it; a page reload clears
// it. Shared across every picker on the page.
const dirCache = new Map<string, FsListing>();

function readLastDir(): string | undefined {
  try {
    return localStorage.getItem(LAST_DIR_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

function rememberDir(path: string): void {
  try {
    localStorage.setItem(LAST_DIR_KEY, path);
  } catch {
    // localStorage can be unavailable (private mode / disabled); ignore.
  }
}

type Row =
  | { kind: "parent"; path: string }
  | { kind: "entry"; entry: FsEntry; full: string };

function toRows(listing: FsListing | null): Row[] {
  if (!listing) return [];
  const rows: Row[] = [];
  if (listing.parent) rows.push({ kind: "parent", path: listing.parent });
  const base = listing.path.replace(/\/$/, "");
  for (const entry of listing.entries) {
    rows.push({ kind: "entry", entry, full: `${base}/${entry.name}` });
  }
  return rows;
}

function DirBrowser({
  initial,
  mode,
  onClose,
  onPick,
}: {
  initial: string | undefined;
  mode: PickerMode;
  onClose: () => void;
  onPick: (path: string) => void;
}): ReactNode {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(480);
  // null = not creating; a string is the in-progress new-folder name.
  const [newName, setNewName] = useState<string | null>(null);
  // Only the newest listing request may write state: browsing on through a slow
  // directory would otherwise let its late reply replace the one now on screen.
  const request = useRef(0);

  const load = (path?: string, opts?: { refresh?: boolean }) => {
    const ticket = ++request.current;
    if (!opts?.refresh && path !== undefined) {
      const cached = dirCache.get(path);
      if (cached) {
        setListing(cached);
        setLoadError(null);
        return;
      }
    }
    fetchDir(path)
      .then((data) => {
        dirCache.set(data.path, data);
        if (path !== undefined) dirCache.set(path, data);
        rememberDir(data.path);
        if (ticket !== request.current) return;
        setListing(data);
        setLoadError(null);
      })
      .catch((e: unknown) => ticket === request.current && setLoadError(String(e)));
  };

  const submitNewFolder = () => {
    const name = (newName ?? "").trim();
    if (!name || !listing) return;
    createDir(listing.path, name)
      .then((data) => {
        dirCache.set(data.path, data);
        rememberDir(data.path);
        setListing(data);
        setLoadError(null);
        setNewName(null);
      })
      .catch((e: unknown) => setLoadError(String(e)));
  };

  useEffect(() => {
    load(initial ?? readLastDir());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // On entering a new directory, reset the scroll position and re-measure the
  // viewport so the window starts at the top of the new listing.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = 0;
    setScrollTop(0);
    if (el.clientHeight) setViewportH(el.clientHeight);
  }, [listing?.path]);

  const rows = toRows(listing);
  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const end = Math.min(
    rows.length,
    Math.ceil((scrollTop + viewportH) / ROW_H) + OVERSCAN,
  );
  const visible = rows.slice(start, end);

  const rowStyle = (index: number): CSSProperties => ({
    position: "absolute",
    top: index * ROW_H,
    left: 0,
    right: 0,
    height: ROW_H,
  });

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <FolderOpen size={16} className="text-cyan-400" />
          <span className="modal-cwd mono">{listing?.path ?? "…"}</span>
          <button
            type="button"
            aria-label="New folder"
            className="rjsf-icon-btn ml-auto"
            disabled={!listing}
            onClick={() => setNewName("")}
          >
            <FolderPlus size={14} />
          </button>
          <button
            type="button"
            aria-label="Refresh"
            className="rjsf-icon-btn"
            disabled={!listing}
            onClick={() => listing && load(listing.path, { refresh: true })}
          >
            <RefreshCw size={13} />
          </button>
          <button
            type="button"
            aria-label="Close"
            className="rjsf-icon-btn"
            onClick={onClose}
          >
            <X size={14} />
          </button>
        </header>

        {newName !== null && (
          <div className="modal-newfolder">
            <FolderPlus size={14} className="text-cyan-500" />
            <input
              autoFocus
              className="newfolder-input mono"
              placeholder="New folder name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submitNewFolder();
                } else if (e.key === "Escape") {
                  setNewName(null);
                }
              }}
            />
            <button
              type="button"
              className="rjsf-add-btn"
              disabled={newName.trim() === ""}
              onClick={submitNewFolder}
            >
              Create
            </button>
            <button
              type="button"
              aria-label="Cancel new folder"
              className="rjsf-icon-btn"
              onClick={() => setNewName(null)}
            >
              <X size={14} />
            </button>
          </div>
        )}

        <div
          className="modal-body"
          ref={scrollRef}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        >
          {(loadError ?? listing?.error) && (
            <div className="modal-error">{loadError ?? listing?.error}</div>
          )}
          <div style={{ position: "relative", height: rows.length * ROW_H }}>
            {visible.map((row, i) => {
              const top = start + i;
              if (row.kind === "parent") {
                return (
                  <button
                    key="__parent"
                    type="button"
                    className="dir-row"
                    style={rowStyle(top)}
                    onClick={() => load(row.path)}
                  >
                    <ArrowUp size={15} className="text-slate-400" />
                    <span className="text-slate-400">..</span>
                  </button>
                );
              }
              const { entry, full } = row;
              return entry.is_dir ? (
                <button
                  key={entry.name}
                  type="button"
                  className="dir-row"
                  style={rowStyle(top)}
                  onClick={() => load(full)}
                  onDoubleClick={() => onPick(full)}
                >
                  <Folder size={15} className="text-cyan-500" />
                  <span>{entry.name}</span>
                </button>
              ) : (
                <button
                  key={entry.name}
                  type="button"
                  className="dir-row file"
                  style={rowStyle(top)}
                  disabled={mode === "dir"}
                  onClick={() => mode !== "dir" && onPick(full)}
                >
                  <FileIcon size={15} className="text-slate-500" />
                  <span className="text-slate-300">{entry.name}</span>
                </button>
              );
            })}
          </div>
          {listing && listing.entries.length === 0 && !listing.error && (
            <div className="modal-empty">Empty folder</div>
          )}
        </div>

        <footer className="modal-foot">
          <span className="text-xs text-slate-500">
            {listing?.truncated
              ? `Showing first ${listing.entries.length} of ${listing.total} — type an exact path to jump`
              : mode === "file"
                ? "Click a folder to open · click a file to select"
                : "Click a folder to open · click a file to select, or pick a folder"}
          </span>
          {mode !== "file" && (
            <button
              type="button"
              className="rjsf-add-btn"
              disabled={!listing}
              onClick={() => listing && onPick(listing.path)}
            >
              Use this folder
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

const PLACEHOLDER: Record<PickerMode, string> = {
  file: "/path/to/file",
  dir: "/path/to/folder",
  any: "/path/to/file or folder",
};

export function PathWidget(props: WidgetProps): ReactNode {
  const { id, value, onChange, disabled, readonly, options } = props;
  const mode: PickerMode =
    options.mode === "file" || options.mode === "dir" ? options.mode : "any";
  const [open, setOpen] = useState(false);
  return (
    <div className="path-widget">
      <input
        id={id}
        className="path-input"
        type="text"
        value={typeof value === "string" ? value : ""}
        disabled={disabled || readonly}
        placeholder={PLACEHOLDER[mode]}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      />
      <button
        type="button"
        className="rjsf-icon-btn"
        aria-label="Browse filesystem"
        disabled={disabled || readonly}
        onClick={() => setOpen(true)}
      >
        <FolderOpen size={15} />
      </button>
      {open && (
        <DirBrowser
          initial={typeof value === "string" ? value : undefined}
          mode={mode}
          onClose={() => setOpen(false)}
          onPick={(path) => {
            onChange(path);
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}
