import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { PROJECT, job } from "../__fixtures__/jobs.ts";
import { fakeTimersPerTest, settle } from "../__fixtures__/timers.ts";
import Projects from "./Projects.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  fetchHistory: vi.fn(),
  fetchProjects: vi.fn(),
  deleteProject: vi.fn(),
  fetchProjectRemoval: vi.fn(),
}));

fakeTimersPerTest();

afterEach(() => {
  vi.restoreAllMocks();
});

test("a project delete that fails says why", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchProjectRemoval).mockResolvedValue([]);
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  vi.mocked(api.deleteProject).mockRejectedValue(
    new Error("409 The jobs are deleted, but the folders of j1 could not be removed"),
  );
  render(<Projects />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete project"));
  await settle();
  expect(screen.getByRole("alert").textContent).toContain("could not be removed");
});

test("the project dialog names every folder that goes, once, and confirms them", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.deleteProject).mockReset();
  vi.mocked(api.deleteProject).mockResolvedValue(undefined);
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [job("j1")] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  const folders = Array.from({ length: 12 }, (_, i) => `/scratch/out${i}`);
  vi.mocked(api.fetchProjectRemoval).mockResolvedValue(
    [...folders, folders[0] as string].map((folder, i) => ({
      job_id: `j${i}`,
      output_folder: folder,
      removes_output: true,
      kept_reason: null,
    })),
  );
  render(<Projects />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete project"));
  await settle();
  const text = confirm.mock.calls[0]?.[0] ?? "";
  expect(text).toContain("/scratch/out11");
  expect(text).toContain("13 jobs");
  expect(api.deleteProject).toHaveBeenCalledWith(PROJECT.id, folders);
});

test("kept folders are cut short, as only information", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.mocked(api.deleteProject).mockReset();
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [job("j1")] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  vi.mocked(api.fetchProjectRemoval).mockResolvedValue(
    Array.from({ length: 12 }, (_, i) => ({
      job_id: `j${i}`,
      output_folder: `/data/kept${i}`,
      removes_output: false,
      kept_reason: "it is used",
    })),
  );
  render(<Projects />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete project"));
  await settle();
  const text = confirm.mock.calls[0]?.[0] ?? "";
  expect(text).toContain("Keeps output folder /data/kept0: it is used");
  expect(text).toContain("…and 7 more kept");
  expect(api.deleteProject).not.toHaveBeenCalled();
});

test("a project is not deleted if it cannot say what would go", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.deleteProject).mockReset();
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  vi.mocked(api.fetchProjectRemoval).mockRejectedValue(new Error("500 nope"));
  render(<Projects />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete project"));
  await settle();
  expect(confirm).not.toHaveBeenCalled();
  expect(api.deleteProject).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain("500 nope");
});

test("folders too many to show are not confirmed, and the dialog says they stay", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.deleteProject).mockReset();
  vi.mocked(api.deleteProject).mockResolvedValue(undefined);
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [job("j1")] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  const folders = Array.from({ length: 200 }, (_, i) => `/scratch/${"d".repeat(60)}/${i}`);
  vi.mocked(api.fetchProjectRemoval).mockResolvedValue(
    folders.map((folder, i) => ({
      job_id: `j${i}`,
      output_folder: folder,
      removes_output: true,
      kept_reason: null,
    })),
  );
  render(<Projects />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete project"));
  await settle();
  const text = confirm.mock.calls[0]?.[0] ?? "";
  const sent = vi.mocked(api.deleteProject).mock.calls[0]?.[1] ?? [];
  expect(sent.length).toBeLessThan(folders.length);
  expect(sent.every((folder) => text.includes(folder))).toBe(true);
  expect(text).toContain(`${folders.length - sent.length} more output folders stay on disk`);
  expect(text.length).toBeLessThan(2000);
});

test("long paths reach the character caps before the count caps", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.deleteProject).mockReset();
  vi.mocked(api.deleteProject).mockResolvedValue(undefined);
  vi.mocked(api.fetchHistory).mockResolvedValue({
    projects: [{ project: PROJECT, jobs: [job("j1")] }],
    ungrouped: [],
  });
  vi.mocked(api.fetchProjects).mockResolvedValue([PROJECT]);
  const path = (length: number, i: number): string => `/${String(i).padEnd(length - 1, "d")}`;
  const going = Array.from({ length: 5 }, (_, i) => path(400, i));
  const kept = Array.from({ length: 5 }, (_, i) => path(100, i));
  vi.mocked(api.fetchProjectRemoval).mockResolvedValue([
    ...going.map((folder, i) => ({
      job_id: `g${i}`,
      output_folder: folder,
      removes_output: true,
      kept_reason: null,
    })),
    ...kept.map((folder, i) => ({
      job_id: `k${i}`,
      output_folder: folder,
      removes_output: false,
      kept_reason: "it is used",
    })),
  ]);
  render(<Projects />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete project"));
  await settle();
  const text = confirm.mock.calls[0]?.[0] ?? "";
  const sent = vi.mocked(api.deleteProject).mock.calls[0]?.[1] ?? [];
  expect(sent).toEqual(going.slice(0, 2));
  expect(sent.every((folder) => text.includes(folder))).toBe(true);
  expect(text).toContain("3 more output folders stay on disk");
  expect(text).toContain("…and 2 more kept");
  expect(text.length).toBeLessThan(2000);
});
