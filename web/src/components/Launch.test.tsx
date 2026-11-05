import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { useStore } from "../store.ts";
import type { CommandMeta } from "../types.ts";
import Launch from "./Launch.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
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
