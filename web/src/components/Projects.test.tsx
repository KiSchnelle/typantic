import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { PROJECT } from "../__fixtures__/jobs.ts";
import Projects from "./Projects.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  fetchHistory: vi.fn(),
  fetchProjects: vi.fn(),
  deleteProject: vi.fn(),
}));

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("a project delete that fails says why", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  vi.mocked(api.deleteProject).mockRejectedValue(
    new Error("409 The jobs are deleted, but the folders of j1 could not be removed"),
  );
  render(<Projects />);
  await act(() => vi.advanceTimersByTimeAsync(0));
  fireEvent.click(screen.getByLabelText("Delete project"));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(screen.getByRole("alert").textContent).toContain("could not be removed");
});
