// Records for component tests, shaped like the server's (see types.ts).

import type { JobRecord, LaunchRequest, Project } from "../types.ts";

export function job(id: string, over: Partial<JobRecord> = {}): JobRecord {
  return {
    id,
    command_key: "app/run",
    app: "app",
    command: "run",
    title: "Run",
    name: null,
    project_id: null,
    backend: "local",
    job_dir: `/jobs/${id}`,
    config_path: `/jobs/${id}/submit_config.json`,
    log_path: `/jobs/${id}/job.log`,
    pid: 4321,
    pid_start: null,
    scheduler_id: null,
    host: null,
    app_version: null,
    status: "running",
    created_at: "2026-09-23T12:00:00Z",
    finished_at: null,
    exit_code: null,
    ...over,
  };
}

export const REQUEST: LaunchRequest = {
  command_key: "app/run",
  backend: "local",
  name: null,
  project_id: null,
  values: {},
  backend_options: {},
};

export const PROJECT: Project = {
  id: "p1",
  name: "Screen A",
  description: "",
  created_at: "2026-09-23T12:00:00Z",
};
