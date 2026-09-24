// Thin fetch wrappers over the typantic web API. The token (if any) comes from
// the page URL (?token=…, the Jupyter pattern) and is sent as a Bearer header.

import type {
  ApiMeta,
  CommandMeta,
  FsListing,
  History,
  JobCompat,
  JobImages,
  JobPage,
  JobQuery,
  JobRecord,
  JsonSchema,
  LaunchPreview,
  LaunchRequest,
  Project,
} from "./types.ts";

const TOKEN = new URLSearchParams(window.location.search).get("token");

function headers(extra?: Record<string, string>): Record<string, string> {
  const base: Record<string, string> = { ...extra };
  if (TOKEN) base["Authorization"] = `Bearer ${TOKEN}`;
  return base;
}

// A failed response as an Error: the API's {"detail": ...} message where it
// sends one, else the raw body.
async function failure(resp: Response): Promise<Error> {
  const body = await resp.text();
  let detail = body;
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") detail = parsed.detail;
  } catch {
    // Not JSON: the body is the message.
  }
  return new Error(`${resp.status} ${detail}`);
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: headers() });
  if (!resp.ok) throw await failure(resp);
  return (await resp.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: body ? headers({ "Content-Type": "application/json" }) : headers(),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw await failure(resp);
  return (await resp.json()) as T;
}

export function fetchMeta(): Promise<ApiMeta> {
  return getJson("/api/meta");
}

export function fetchCommands(): Promise<CommandMeta[]> {
  return getJson("/api/commands");
}

export function fetchSchema(key: string): Promise<JsonSchema> {
  return getJson(`/api/commands/${key}/schema`);
}

export function fetchJobs(query: JobQuery = {}): Promise<JobPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "" && value !== false) {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return getJson(`/api/jobs${qs ? `?${qs}` : ""}`);
}

export function fetchHistory(): Promise<History> {
  return getJson("/api/history");
}

export function fetchProjects(): Promise<Project[]> {
  return getJson("/api/projects");
}

export function createProject(name: string, description = ""): Promise<Project> {
  return postJson("/api/projects", { name, description });
}

export async function deleteProject(id: string): Promise<void> {
  const resp = await fetch(`/api/projects/${id}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!resp.ok) throw await failure(resp);
}

export function fetchDir(path?: string): Promise<FsListing> {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  return getJson(`/api/fs${q}`);
}

// Create one folder under `path`; resolves to the new folder's (empty) listing.
export function createDir(path: string, name: string): Promise<FsListing> {
  return postJson("/api/fs/mkdir", { path, name });
}

export function fetchImages(id: string): Promise<JobImages> {
  return getJson(`/api/jobs/${id}/images`);
}

// <img> can't send an Authorization header, so the token rides as a query param.
export function imageSrc(url: string): string {
  return TOKEN ? `${url}&token=${encodeURIComponent(TOKEN)}` : url;
}

// Grid thumbnail: same image, downscaled server-side to `w` px on its longest
// edge (so the browser transfers/decodes KB, not the full-resolution original).
export function thumbSrc(url: string, w: number): string {
  return imageSrc(`${url}&w=${w}`);
}

export function fetchJob(id: string): Promise<JobRecord> {
  return getJson(`/api/jobs/${id}`);
}

export function launchJob(request: LaunchRequest): Promise<JobRecord> {
  return postJson("/api/launch", request);
}

export function previewLaunch(request: LaunchRequest): Promise<LaunchPreview> {
  return postJson("/api/preview", request);
}

export function cancelJob(id: string): Promise<JobRecord> {
  return postJson(`/api/jobs/${id}/cancel`);
}

// The full launch request behind a job, to pre-fill the form on Clone and to
// show the submitted config / backend options in the job detail.
export function fetchJobRequest(id: string): Promise<LaunchRequest> {
  return getJson(`/api/jobs/${id}/request`);
}

// Whether a job's settings still fit the installed version of its command; a
// setting the installed command does not have locks Clone and Restart.
export function fetchJobCompat(id: string): Promise<JobCompat> {
  return getJson(`/api/jobs/${id}/compat`);
}

// Remove a job from history (cancels it first if still active, then deletes it).
export async function deleteJob(id: string): Promise<void> {
  const resp = await fetch(`/api/jobs/${id}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!resp.ok) throw await failure(resp);
}

// Re-run a terminal job in place. Without `request` it resubmits the same
// settings; with `request` it re-runs the same job with edited settings.
export function restartJob(
  id: string,
  request?: LaunchRequest,
): Promise<JobRecord> {
  return postJson(`/api/jobs/${id}/restart`, request);
}

// Open the live log-tail WebSocket for a job. The token rides as a query param
// (WebSocket has no custom-header API in the browser).
export function openLogSocket(id: string): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const q = TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : "";
  return new WebSocket(`${proto}://${window.location.host}/ws/jobs/${id}/log${q}`);
}

// Each frame on the log socket is a JSON envelope: {"log": "..."} for output,
// {"reset": true} when the log started over (replaced by a restart in place, or
// truncated), {"end": {...}} once the job is terminal. Wrapping the log in an
// envelope means a log line can never be mistaken for a signal.
export function logChunk(data: string): string {
  try {
    const frame = JSON.parse(data) as { log?: unknown };
    return typeof frame.log === "string" ? frame.log : "";
  } catch {
    return "";
  }
}

// The log started over: what is shown so far is no longer in it.
export function isResetFrame(data: string): boolean {
  try {
    return (JSON.parse(data) as { reset?: unknown }).reset === true;
  } catch {
    return false;
  }
}

// The server closes the socket after the {"end": ...} frame; that close is
// expected and must not trigger a reconnect.
export function isEndFrame(data: string): boolean {
  try {
    return (JSON.parse(data) as { end?: unknown }).end !== undefined;
  } catch {
    return false;
  }
}
