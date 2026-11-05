import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { job } from "../__fixtures__/jobs.ts";
import { JOB_STATUSES } from "../types.ts";
import type { JobPage, JobQuery } from "../types.ts";
import Jobs from "./Jobs.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  fetchJobs: vi.fn(),
  deleteJob: vi.fn(),
  fetchJobRemoval: vi.fn(),
}));

function page(count: number, total: number, offset = 0): JobPage {
  return {
    jobs: Array.from({ length: count }, (_, i) => job(`j${offset + i}`)),
    total,
  };
}

const NO_OUTPUT = {
  job_id: "j0",
  output_folder: null,
  removes_output: false,
  kept_reason: null,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api.fetchJobRemoval).mockReset();
  vi.mocked(api.deleteJob).mockReset();
  vi.mocked(api.fetchJobRemoval).mockResolvedValue(NO_OUTPUT);
  vi.mocked(api.deleteJob).mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function settle(): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(0));
}

test("losing the last job on the last page goes back a page", async () => {
  // Page 2 held one job; once it was deleted the list sat on an empty page 2
  // with the pagination hidden, and no way back but reloading.
  let total = 26;
  vi.mocked(api.fetchJobs).mockImplementation((query: JobQuery = {}) => {
    const offset = query.offset ?? 0;
    return Promise.resolve(page(Math.max(0, Math.min(25, total - offset)), total, offset));
  });
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByText("Next →"));
  await settle();
  expect(screen.getAllByLabelText("Delete job")).toHaveLength(1);
  total = 25; // the last job is gone
  await act(() => vi.advanceTimersByTimeAsync(2000)); // the next poll sees it
  await settle();
  expect(screen.getAllByLabelText("Delete job")).toHaveLength(25);
  expect(screen.getByText("Page 1 / 1")).toBeTruthy();
});

test("a delete that fails says why", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  vi.mocked(api.deleteJob).mockRejectedValue(
    new Error("409 Job j0 is deleted, but its folder could not be removed completely"),
  );
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(screen.getByRole("alert").textContent).toContain("could not be removed");
});

test("the status filter offers every job status", async () => {
  vi.mocked(api.fetchJobs).mockResolvedValue(page(0, 0));
  render(<Jobs />);
  await settle();
  const options = [...screen.getByLabelText("Status").querySelectorAll("option")];
  expect(options.map((o) => o.value)).toEqual(["", ...JOB_STATUSES]);
});

test("each job shows the app version it ran with, when known", async () => {
  vi.mocked(api.fetchJobs).mockResolvedValue({
    jobs: [job("j0", { app_version: "1.4.2" }), job("j1")],
    total: 2,
  });
  render(<Jobs />);
  await settle();
  expect(screen.getAllByText(/^v\d/).map((el) => el.textContent)).toEqual(["v1.4.2"]);
});

test("the delete dialog shows the output folder, and the delete confirms that path", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  vi.mocked(api.fetchJobRemoval).mockResolvedValue({
    ...NO_OUTPUT,
    output_folder: "/scratch/me/out",
    removes_output: true,
  });
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(confirm.mock.calls[0]?.[0]).toContain(
    "Deletes this output folder and everything in it:\n/scratch/me/out",
  );
  expect(api.deleteJob).toHaveBeenCalledWith("j0", ["/scratch/me/out"]);
});

test("declining the dialog deletes nothing", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(api.deleteJob).not.toHaveBeenCalled();
});

test("a job with no output folder outside its own confirms none", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(confirm.mock.calls[0]?.[0]).not.toContain("output folder");
  expect(api.deleteJob).toHaveBeenCalledWith("j0", []);
});

test("the delete dialog says when a folder stays, and why, and confirms nothing", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  vi.mocked(api.fetchJobRemoval).mockResolvedValue({
    ...NO_OUTPUT,
    output_folder: "/data",
    kept_reason: "it is a top-level folder",
  });
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(confirm.mock.calls[0]?.[0]).toContain(
    "Keeps output folder /data: it is a top-level folder",
  );
  expect(api.deleteJob).toHaveBeenCalledWith("j0", []);
});

test("nothing is deleted if it cannot say what would go", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  vi.mocked(api.fetchJobRemoval).mockRejectedValue(new Error("500 nope"));
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(confirm).not.toHaveBeenCalled();
  expect(api.deleteJob).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain("500 nope");
});

test("a double click asks once", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(1, 1));
  render(<Jobs />);
  await settle();
  fireEvent.click(screen.getByLabelText("Delete job"));
  fireEvent.click(screen.getByLabelText("Delete job"));
  await settle();
  expect(confirm).toHaveBeenCalledOnce();
  expect(api.deleteJob).toHaveBeenCalledOnce();
});

test("a request that hangs does not stop another job being deleted", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.fetchJobs).mockResolvedValue(page(2, 2));
  vi.mocked(api.fetchJobRemoval)
    .mockReturnValueOnce(new Promise(() => undefined))
    .mockResolvedValue(NO_OUTPUT);
  render(<Jobs />);
  await settle();
  const [first, second] = screen.getAllByLabelText("Delete job");
  fireEvent.click(first as HTMLElement);
  fireEvent.click(second as HTMLElement);
  await settle();
  expect(api.deleteJob).toHaveBeenCalledOnce();
});
