import { create } from "zustand";
import type { BackendMeta, CommandMeta, LaunchRequest, Project } from "./types.ts";

export type View = "launch" | "jobs" | "projects";

interface State {
  view: View;
  title: string;
  backends: BackendMeta[];
  commands: CommandMeta[];
  projects: Project[];
  selectedCommandKey: string | null;
  selectedJobId: string | null;
  // A past job's request awaiting the Launch form, set by Clone (see cloneFrom)
  // or by an edit-and-restart (see editForRestart).
  prefill: LaunchRequest | null;
  // When set, the Launch form is editing this job's settings to restart it in
  // place, rather than launching a brand-new job.
  restartJobId: string | null;
  setView: (view: View) => void;
  setTitle: (title: string) => void;
  setBackends: (backends: BackendMeta[]) => void;
  setCommands: (commands: CommandMeta[]) => void;
  setProjects: (projects: Project[]) => void;
  selectCommand: (key: string | null) => void;
  cloneFrom: (request: LaunchRequest) => void;
  editForRestart: (id: string, request: LaunchRequest) => void;
  clearPrefill: () => void;
  openJob: (id: string) => void;
  closeJob: () => void;
}

export const useStore = create<State>((set) => ({
  view: "launch",
  title: "typantic web",
  backends: [],
  commands: [],
  projects: [],
  selectedCommandKey: null,
  selectedJobId: null,
  prefill: null,
  restartJobId: null,
  // Switching to a tab always resets it: "Launch" returns to the command
  // catalog (not a half-filled form), "Jobs" to the list.
  setView: (view) =>
    set({
      view,
      selectedJobId: null,
      selectedCommandKey: null,
      prefill: null,
      restartJobId: null,
    }),
  setTitle: (title) => set({ title }),
  setBackends: (backends) => set({ backends }),
  setCommands: (commands) => set({ commands }),
  setProjects: (projects) => set({ projects }),
  // Picking a command from the catalog starts a fresh form (drop any prefill /
  // restart mode).
  selectCommand: (key) =>
    set({ selectedCommandKey: key, prefill: null, restartJobId: null }),
  // Clone: open the command's form pre-filled from a past job's request, to
  // launch a NEW job.
  cloneFrom: (request) =>
    set({
      view: "launch",
      selectedJobId: null,
      selectedCommandKey: request.command_key,
      prefill: request,
      restartJobId: null,
    }),
  // Edit-and-restart: same pre-filled form, but Submit restarts the given job in
  // place instead of launching a new one.
  editForRestart: (id, request) =>
    set({
      view: "launch",
      selectedJobId: null,
      selectedCommandKey: request.command_key,
      prefill: request,
      restartJobId: id,
    }),
  clearPrefill: () => set({ prefill: null }),
  openJob: (id) => set({ view: "jobs", selectedJobId: id, restartJobId: null }),
  closeJob: () => set({ selectedJobId: null }),
}));
