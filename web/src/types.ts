// Mirrors typantic/web/models.py. Kept in sync by hand (small, stable surface).

// A backend is identified by its registry key (e.g. "local", "slurm", "docker").
export type Backend = string;

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export const TERMINAL_STATUSES: JobStatus[] = ["done", "failed", "cancelled"];

export interface CommandMeta {
  app: string;
  command: string;
  argv: string[];
  title: string;
  description: string;
  default_backend: Backend;
  key: string;
}

// A JSON Schema object (from the CLI's --schema, or a backend's options); handed
// straight to RJSF.
export type JsonSchema = Record<string, unknown>;

// One installed backend and its options JSON Schema (null = no options).
export interface BackendMeta {
  key: string;
  options_schema: JsonSchema | null;
}

export interface Meta {
  title: string;
  version: string;
  backends: BackendMeta[];
}

export interface LaunchRequest {
  command_key: string;
  backend: Backend;
  name?: string | null;
  project_id?: string | null;
  values: Record<string, unknown>;
  backend_options: Record<string, unknown>;
}

export interface JobRecord {
  id: string;
  command_key: string;
  app: string;
  command: string;
  title: string;
  name: string | null;
  project_id: string | null;
  backend: Backend;
  job_dir: string;
  config_path: string;
  log_path: string;
  pid: number | null;
  scheduler_id: string | null;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  exit_code: number | null;
}

export interface LaunchPreview {
  config: string;
  argv: string[];
  // Always present: schedulers render a submit script, the process backends the
  // wrapped shell command.
  script: string;
}

export interface JobPage {
  jobs: JobRecord[];
  total: number;
}

export interface JobQuery {
  status?: JobStatus | "";
  app?: string;
  backend?: string;
  project?: string;
  ungrouped?: boolean;
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface ProjectGroup {
  project: Project;
  jobs: JobRecord[];
}

export interface History {
  projects: ProjectGroup[];
  ungrouped: JobRecord[];
}

export interface FsEntry {
  name: string;
  is_dir: boolean;
}

export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
  error: string | null;
  total: number;
  truncated: boolean;
}

export interface JobImage {
  name: string;
  root: number;
  url: string;
}
