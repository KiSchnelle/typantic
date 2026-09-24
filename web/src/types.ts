// AUTO-GENERATED from typantic/web/models.py by scripts/gen_types.py.
// Do not edit by hand: run `make gen-types` after changing the models.
// `python scripts/gen_types.py --check` fails CI if this file is out of date.

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

// Every status, in lifecycle order: the Jobs filter lists these.
export const JOB_STATUSES: JobStatus[] = ["queued", "running", "done", "failed", "cancelled"];

export const TERMINAL_STATUSES: JobStatus[] = ["done", "failed", "cancelled"];

// A JSON Schema object (a command's --schema, or a backend's options); handed
// straight to RJSF.
export type JsonSchema = Record<string, unknown>;

// The jobs-list query params, assembled client-side (no server model).
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

export interface CommandMeta {
  app: string;
  command: string;
  argv: string[];
  title: string;
  description: string;
  default_backend: string;
  key: string;
}

export interface BackendMeta {
  key: string;
  options_schema: Record<string, unknown> | null;
}

export interface ApiMeta {
  title: string;
  version: string;
  backends: BackendMeta[];
  wordmark_lead: string;
  wordmark_rest: string;
  icon: string | null;
  accent: string | null;
}

export interface LaunchRequest {
  command_key: string;
  backend: string;
  name: string | null;
  project_id: string | null;
  values: Record<string, unknown>;
  backend_options: Record<string, unknown>;
}

export interface LaunchPreview {
  config: string;
  argv: string[];
  script: string;
}

export interface JobRecord {
  id: string;
  command_key: string;
  app: string;
  command: string;
  title: string;
  name: string | null;
  project_id: string | null;
  backend: string;
  job_dir: string;
  config_path: string;
  log_path: string;
  pid: number | null;
  pid_start: number | null;
  scheduler_id: string | null;
  host: string | null;
  app_version: string | null;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  exit_code: number | null;
}

export interface JobCompat {
  app_version: string | null;
  installed_version: string | null;
  unknown_settings: string[];
}

export interface JobPage {
  jobs: JobRecord[];
  total: number;
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

export interface JobImages {
  images: JobImage[];
  truncated: boolean;
}
