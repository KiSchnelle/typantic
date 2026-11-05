import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { useStore } from "../store.ts";
import { job } from "../__fixtures__/jobs.ts";
import type { CommandMeta, JobRecord, LaunchPreview, Project } from "../types.ts";
import Launch from "./Launch.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  createProject: vi.fn(),
  fetchProjects: vi.fn(),
  fetchSchema: vi.fn(),
  launchJob: vi.fn(),
  previewLaunch: vi.fn(),
  restartJob: vi.fn(),
}));

function command(key: string, title: string): CommandMeta {
  return {
    app: "app",
    command: key,
    argv: [],
    title,
    description: "",
    default_backend: "local",
    key,
  };
}

const ALPHA = command("app/alpha", "Alpha");
const BETA = command("app/beta", "Beta");

const initial = useStore.getState();

beforeEach(() => {
  useStore.setState({ ...initial, commands: [ALPHA, BETA] }, true);
  vi.mocked(api.createProject).mockReset();
  vi.mocked(api.fetchProjects).mockReset();
  vi.mocked(api.fetchSchema).mockReset();
  vi.mocked(api.fetchSchema).mockResolvedValue({ type: "object", properties: {} });
  vi.mocked(api.launchJob).mockReset();
  vi.mocked(api.previewLaunch).mockReset();
  vi.mocked(api.restartJob).mockReset();
});

afterEach(cleanup);

async function openForm(): Promise<void> {
  useStore.setState({ selectedCommandKey: ALPHA.key });
  render(<Launch />);
  await screen.findByText("Preview");
}

test("a schema that fails to load shows the server's message, without JavaScript's prefix", async () => {
  vi.mocked(api.fetchSchema).mockRejectedValue(new Error("500 no schema"));
  useStore.setState({ selectedCommandKey: ALPHA.key });
  render(<Launch />);
  expect((await screen.findByText(/500 no schema/)).textContent).toBe("500 no schema");
});

test("a launch that fails shows the server's message, without JavaScript's prefix", async () => {
  vi.mocked(api.launchJob).mockRejectedValue(new Error("409 busy"));
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  expect((await screen.findByText(/409 busy/)).textContent).toBe("409 busy");
});

test("a preview that fails shows the server's message, without JavaScript's prefix", async () => {
  vi.mocked(api.previewLaunch).mockRejectedValue(new Error("422 invalid"));
  await openForm();
  fireEvent.click(screen.getByText("Preview"));
  expect((await screen.findByText(/422 invalid/)).textContent).toBe("422 invalid");
});

const CREATED: Project = {
  id: "p-new",
  name: "New one",
  description: "",
  created_at: "2026-09-23T12:00:00Z",
};

async function createProjectInForm(): Promise<HTMLSelectElement> {
  vi.mocked(api.createProject).mockResolvedValue(CREATED);
  await openForm();
  fireEvent.click(screen.getByLabelText("New project"));
  fireEvent.change(screen.getByPlaceholderText("New project name"), {
    target: { value: CREATED.name },
  });
  fireEvent.click(screen.getByText("Create"));
  await screen.findByRole("option", { name: CREATED.name });
  return screen.getByRole("combobox");
}

test("a project created in the form is selected before the list refetch answers", async () => {
  vi.mocked(api.fetchProjects).mockReturnValue(new Promise(() => undefined));
  const select = await createProjectInForm();
  expect(select.value).toBe(CREATED.id);
});

test("a refetch that fails after a create keeps the project selected and rejects nothing", async () => {
  const unhandled = vi.fn();
  const { process } = globalThis as unknown as {
    process: {
      on(event: string, listener: () => void): void;
      off(event: string, listener: () => void): void;
    };
  };
  process.on("unhandledRejection", unhandled);
  try {
    vi.mocked(api.fetchProjects).mockRejectedValue(new Error("502 Bad Gateway"));
    const select = await createProjectInForm();
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(select.value).toBe(CREATED.id);
    expect(unhandled).not.toHaveBeenCalled();
  } finally {
    process.off("unhandledRejection", unhandled);
  }
});

test("a poll that already brought the created project does not list it twice", async () => {
  vi.mocked(api.fetchProjects).mockReturnValue(new Promise(() => undefined));
  useStore.setState({ projects: [CREATED] });
  await createProjectInForm();
  expect(screen.getAllByRole("option", { name: CREATED.name })).toHaveLength(1);
});

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function previewOf(config: string): LaunchPreview {
  return { config, argv: [], script: "" };
}

// Lets a settled reply run its continuation and React render the result.
async function land(settle: () => void): Promise<void> {
  await act(async () => {
    settle();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function toCatalog(): void {
  fireEvent.click(screen.getByText("All commands"));
}

async function pick(title: string): Promise<void> {
  fireEvent.click(screen.getByText(title));
  await screen.findByText("Preview");
}

function button(text: string): HTMLButtonElement {
  const found = screen.getByText(text).closest("button");
  if (!found) throw new Error(`${text} is not on a button`);
  return found;
}

test("a preview answering after a switch to another command opens nothing", async () => {
  const reply = deferred<LaunchPreview>();
  vi.mocked(api.previewLaunch).mockReturnValue(reply.promise);
  await openForm();
  fireEvent.click(screen.getByText("Preview"));
  toCatalog();
  await land(() => reply.resolve(previewOf("alpha config")));
  await pick("Beta");
  expect(screen.queryByText("alpha config")).toBeNull();
  expect(screen.queryByText(/Submission preview/)).toBeNull();
});

test("a preview answering after leaving and reopening the same command opens nothing", async () => {
  const reply = deferred<LaunchPreview>();
  vi.mocked(api.previewLaunch).mockReturnValue(reply.promise);
  await openForm();
  fireEvent.click(screen.getByText("Preview"));
  toCatalog();
  await pick("Alpha");
  await land(() => reply.resolve(previewOf("old config")));
  expect(screen.queryByText("old config")).toBeNull();
});

test("a preview left open does not follow the user to another command", async () => {
  vi.mocked(api.previewLaunch).mockResolvedValue(previewOf("alpha config"));
  await openForm();
  fireEvent.click(screen.getByText("Preview"));
  await screen.findByText("alpha config");
  act(() => useStore.getState().selectCommand(null));
  await pick("Beta");
  expect(screen.queryByText("alpha config")).toBeNull();
});

test("a stale preview that fails puts nothing on the form now showing", async () => {
  const reply = deferred<LaunchPreview>();
  vi.mocked(api.previewLaunch).mockReturnValue(reply.promise);
  await openForm();
  fireEvent.click(screen.getByText("Preview"));
  toCatalog();
  await pick("Beta");
  await land(() => reply.reject(new Error("422 alpha invalid")));
  expect(screen.queryByText(/alpha invalid/)).toBeNull();
});

test("a stale preview does not release the reopened form's own preview", async () => {
  const stale = deferred<LaunchPreview>();
  const fresh = deferred<LaunchPreview>();
  vi.mocked(api.previewLaunch)
    .mockReturnValueOnce(stale.promise)
    .mockReturnValueOnce(fresh.promise);
  await openForm();
  fireEvent.click(screen.getByText("Preview"));
  toCatalog();
  await pick("Alpha");
  expect(button("Preview").disabled).toBe(false);
  fireEvent.click(screen.getByText("Preview"));
  await land(() => stale.resolve(previewOf("stale config")));
  expect(button("…").disabled).toBe(true);
  expect(screen.queryByText("stale config")).toBeNull();
  await land(() => fresh.resolve(previewOf("fresh config")));
  expect(screen.getByText("fresh config")).toBeTruthy();
  expect(button("Preview").disabled).toBe(false);
});

test("a submit in flight leaves the next command's Launch button free", async () => {
  vi.mocked(api.launchJob).mockReturnValue(new Promise(() => undefined));
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await pick("Beta");
  expect(button("Launch").disabled).toBe(false);
});

test("a submit answering after a switch still opens the job it launched", async () => {
  const reply = deferred<JobRecord>();
  vi.mocked(api.launchJob).mockReturnValue(reply.promise);
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await land(() => reply.resolve(job("j1")));
  expect(useStore.getState().selectedJobId).toBe("j1");
  expect(useStore.getState().view).toBe("jobs");
});

test("a late launch failure shows on the catalog, names its command and outlives the next form", async () => {
  const reply = deferred<JobRecord>();
  vi.mocked(api.launchJob).mockReturnValue(reply.promise);
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await land(() => reply.reject(new Error("409 busy")));
  expect(screen.getByRole("alert").textContent).toContain("Alpha did not launch: 409 busy");

  await pick("Beta");
  expect(screen.getByRole("alert").textContent).toContain("Alpha did not launch: 409 busy");
  expect(button("Launch").disabled).toBe(false);

  fireEvent.click(screen.getByText("Dismiss"));
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a late failure on a form already showing is not taken for that form's own", async () => {
  const reply = deferred<JobRecord>();
  vi.mocked(api.launchJob).mockReturnValueOnce(reply.promise);
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await pick("Beta");
  await land(() => reply.reject(new Error("409 busy")));
  expect(screen.getByRole("alert").textContent).toContain("Alpha did not launch: 409 busy");
});

test("a stale submit that fails does not release the reopened form's own submit", async () => {
  const stale = deferred<JobRecord>();
  vi.mocked(api.launchJob)
    .mockReturnValueOnce(stale.promise)
    .mockReturnValueOnce(new Promise(() => undefined));
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await pick("Alpha");
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  await land(() => stale.reject(new Error("409 busy")));
  expect(button("Launching…").disabled).toBe(true);
  expect(screen.getByRole("alert").textContent).toContain("Alpha did not launch: 409 busy");
});

test("two late failures both stay on screen", async () => {
  const first = deferred<JobRecord>();
  const second = deferred<JobRecord>();
  vi.mocked(api.launchJob)
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  await openForm();
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await pick("Beta");
  fireEvent.click(screen.getByText("Launch"));
  await screen.findByText("Launching…");
  toCatalog();
  await land(() => {
    first.reject(new Error("409 first"));
    second.reject(new Error("409 second"));
  });
  const shown = screen.getByRole("alert").textContent;
  expect(shown).toContain("Alpha did not launch: 409 first");
  expect(shown).toContain("Beta did not launch: 409 second");
});

test("a late restart failure says the restart failed", async () => {
  const reply = deferred<JobRecord>();
  vi.mocked(api.restartJob).mockReturnValue(reply.promise);
  useStore.setState({ restartJobId: "j1" });
  await openForm();
  fireEvent.click(screen.getByText("Restart job"));
  await screen.findByText("Restarting…");
  toCatalog();
  await land(() => reply.reject(new Error("409 still running")));
  expect(screen.getByRole("alert").textContent).toContain(
    "Alpha did not restart: 409 still running",
  );
});
