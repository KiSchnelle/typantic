import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { useStore } from "../store.ts";
import type { CommandMeta, Project } from "../types.ts";
import Launch from "./Launch.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  createProject: vi.fn(),
  fetchProjects: vi.fn(),
  fetchSchema: vi.fn(),
  launchJob: vi.fn(),
  previewLaunch: vi.fn(),
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

const initial = useStore.getState();

beforeEach(() => {
  useStore.setState({ ...initial, commands: [ALPHA] }, true);
  vi.mocked(api.createProject).mockReset();
  vi.mocked(api.fetchProjects).mockReset();
  vi.mocked(api.fetchSchema).mockReset();
  vi.mocked(api.fetchSchema).mockResolvedValue({ type: "object", properties: {} });
  vi.mocked(api.launchJob).mockReset();
  vi.mocked(api.previewLaunch).mockReset();
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
