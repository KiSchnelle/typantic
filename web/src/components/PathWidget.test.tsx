import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { WidgetProps } from "@rjsf/utils";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import type { FsListing } from "../types.ts";
import { PathWidget } from "./PathWidget.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  fetchDir: vi.fn(),
  createDir: vi.fn(),
}));

function listing(path: string, parent: string | null, dirs: string[] = []): FsListing {
  return {
    path,
    parent,
    entries: dirs.map((name) => ({ name, is_dir: true })),
    error: null,
    total: dirs.length,
    truncated: false,
  };
}

// The widget's listing cache lives as long as the page, so each test browses
// paths of its own.
function openPicker(value: string, onChange = vi.fn()): void {
  const props = { id: "p", value, onChange, options: {} } as unknown as WidgetProps;
  render(<PathWidget {...props} />);
  fireEvent.click(screen.getByLabelText("Browse filesystem"));
}

function createFolder(name: string): void {
  fireEvent.click(screen.getByLabelText("New folder"));
  fireEvent.change(screen.getByPlaceholderText("New folder name"), {
    target: { value: name },
  });
  fireEvent.click(screen.getByText("Create"));
}

beforeEach(() => {
  vi.mocked(api.fetchDir).mockReset();
  vi.mocked(api.createDir).mockReset();
});

afterEach(cleanup);

test("a listing that fails shows the server's message, without JavaScript's prefix", async () => {
  vi.mocked(api.fetchDir).mockRejectedValue(new Error("403 denied"));
  openPicker("/errors/list");
  expect((await screen.findByText(/403 denied/)).textContent).toBe("403 denied");
});

test("a folder that cannot be created shows the server's message, without JavaScript's prefix", async () => {
  vi.mocked(api.fetchDir).mockResolvedValue(listing("/errors/mkdir", "/errors"));
  vi.mocked(api.createDir).mockRejectedValue(new Error("409 exists"));
  openPicker("/errors/mkdir");
  await screen.findByText("/errors/mkdir");
  createFolder("x");
  expect((await screen.findByText(/409 exists/)).textContent).toBe("409 exists");
});
