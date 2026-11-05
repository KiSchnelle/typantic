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

test("a folder created in the picker shows when going back up", async () => {
  vi.mocked(api.fetchDir)
    .mockResolvedValueOnce(listing("/up/home", "/up"))
    .mockResolvedValueOnce(listing("/up/home", "/up", ["made"]));
  vi.mocked(api.createDir).mockResolvedValue(listing("/up/home/made", "/up/home"));
  openPicker("/up/home");
  await screen.findByText("/up/home");
  createFolder("made");
  await screen.findByText("/up/home/made");
  fireEvent.click(screen.getByText(".."));
  expect(await screen.findByText("made")).toBeTruthy();
  expect(api.fetchDir).toHaveBeenLastCalledWith("/up/home");
});

test("reopening the picker at another spelling of the parent shows the new folder", async () => {
  vi.mocked(api.fetchDir)
    .mockResolvedValueOnce(listing("/typed/home", "/typed"))
    .mockResolvedValueOnce(listing("/typed/home", "/typed", ["made"]));
  vi.mocked(api.createDir).mockResolvedValue(listing("/typed/home/made", "/typed/home"));
  openPicker("/typed/home/");
  await screen.findByText("/typed/home");
  createFolder("made");
  await screen.findByText("/typed/home/made");
  fireEvent.click(screen.getByLabelText("Close"));
  fireEvent.click(screen.getByLabelText("Browse filesystem"));
  expect(await screen.findByText("made")).toBeTruthy();
});

test("the parent the server names is refreshed when it differs from the folder shown", async () => {
  vi.mocked(api.fetchDir)
    .mockResolvedValueOnce(listing("/link/real", "/link"))
    .mockResolvedValueOnce(listing("/link/alias", "/link"))
    .mockResolvedValueOnce(listing("/link/real", "/link", ["made"]));
  vi.mocked(api.createDir).mockResolvedValue(listing("/link/real/made", "/link/real"));
  openPicker("/link/real");
  await screen.findByText("/link/real");
  fireEvent.click(screen.getByLabelText("Close"));
  cleanup();
  openPicker("/link/alias");
  await screen.findByText("/link/alias");
  createFolder("made");
  await screen.findByText("/link/real/made");
  fireEvent.click(screen.getByText(".."));
  expect(await screen.findByText("made")).toBeTruthy();
});

test("a double click on a folder picks nothing", async () => {
  const onChange = vi.fn();
  vi.mocked(api.fetchDir).mockResolvedValue(listing("/dbl/home", "/dbl", ["child"]));
  openPicker("/dbl/home", onChange);
  fireEvent.doubleClick(await screen.findByText("child"));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByText("/dbl/home")).toBeTruthy();
});

test("Use this folder picks the folder on screen", async () => {
  const onChange = vi.fn();
  vi.mocked(api.fetchDir).mockResolvedValue(listing("/use/home", "/use"));
  openPicker("/use/home", onChange);
  await screen.findByText("/use/home");
  fireEvent.click(screen.getByText("Use this folder"));
  expect(onChange).toHaveBeenCalledWith("/use/home");
});
