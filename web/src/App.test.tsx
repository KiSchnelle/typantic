import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "./api.ts";
import App from "./App.tsx";
import { useStore } from "./store.ts";
import type { ApiMeta, CommandMeta } from "./types.ts";
import { fakeTimersPerTest, settle } from "./__fixtures__/timers.ts";

vi.mock("./api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api.ts")>()),
  fetchMeta: vi.fn(),
  fetchCommands: vi.fn(),
  fetchProjects: vi.fn(),
}));

const UNBRANDED: ApiMeta = {
  title: "typantic web",
  version: "0.8.0",
  backends: [],
  wordmark_lead: "typantic",
  wordmark_rest: "web",
  icon: null,
  accent: null,
};
const CATCHEM: ApiMeta = {
  ...UNBRANDED,
  title: "catchEM",
  wordmark_lead: "catchEM",
  wordmark_rest: "",
  icon: "data:image/svg+xml;base64,PHN2Zy8+",
  accent: "#5AA9FF",
};

const initial = useStore.getState();

fakeTimersPerTest();

beforeEach(() => {
  useStore.setState(initial, true);
  document.head.innerHTML = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">';
  vi.mocked(api.fetchCommands).mockResolvedValue([]);
  vi.mocked(api.fetchProjects).mockResolvedValue([]);
});

afterEach(() => {
  document.documentElement.removeAttribute("style");
});

function sidebarMark(): string | null {
  return document.querySelector("aside img")?.getAttribute("src") ?? null;
}

test("a brand's name, mark and accent are what the dashboard shows", async () => {
  vi.mocked(api.fetchMeta).mockResolvedValue(CATCHEM);
  render(<App />);
  await settle();
  expect(screen.getByText("catchEM")).toBeTruthy();
  expect(sidebarMark()).toBe(CATCHEM.icon);
  expect(document.title).toBe("catchEM");
  expect(document.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe(CATCHEM.icon);
  expect(document.documentElement.style.getPropertyValue("--color-brand-400")).toBe("#5AA9FF");
});

test("without a brand the dashboard is typantic's", async () => {
  vi.mocked(api.fetchMeta).mockResolvedValue(UNBRANDED);
  render(<App />);
  await settle();
  expect(screen.getByText("typantic")).toBeTruthy();
  expect(screen.getByText("web")).toBeTruthy();
  expect(sidebarMark()).toBe("/favicon.svg");
  expect(document.documentElement.style.getPropertyValue("--color-brand-400")).toBe("");
});

test("the brand and backends are asked for until the server answers", async () => {
  // Asked once, a page loaded during a server restart kept no backends -- and
  // no brand -- for the whole session.
  vi.mocked(api.fetchMeta)
    .mockRejectedValueOnce(new Error("502 Bad Gateway"))
    .mockResolvedValue(CATCHEM);
  render(<App />);
  await settle();
  expect(sidebarMark()).toBe("/favicon.svg");
  await settle(2000);
  expect(sidebarMark()).toBe(CATCHEM.icon);
  await settle(10_000);
  expect(api.fetchMeta).toHaveBeenCalledTimes(2); // then no more
});

test("until the server answers, the tab keeps the page's title", async () => {
  // The server writes the brand's title into the page; the store's typantic
  // default overwrote it until /api/meta answered, and for good if it never did.
  document.title = "catchEM";
  vi.mocked(api.fetchMeta).mockRejectedValue(new Error("401 Unauthorized"));
  render(<App />);
  await settle();
  expect(document.title).toBe("catchEM");
});

test("the main column may shrink, so a long log line scrolls in the log, not the page", async () => {
  // jsdom lays nothing out, so this pins the class that does. Without it the
  // column, a flex item, grew to its widest content: a 900-character progress
  // bar in a job's log pushed the job's Clone and Restart ~5000px off-screen.
  vi.mocked(api.fetchMeta).mockResolvedValue(UNBRANDED);
  render(<App />);
  await settle();
  expect(screen.getByRole("main").parentElement?.classList.contains("min-w-0")).toBe(true);
});

const COMMAND: CommandMeta = {
  app: "app",
  command: "run",
  argv: ["app", "run"],
  title: "Run",
  description: "",
  default_backend: "local",
  key: "app/run",
};

test("a command edited on the server reaches the catalog under the same key", async () => {
  vi.mocked(api.fetchMeta).mockResolvedValue(UNBRANDED);
  vi.mocked(api.fetchCommands)
    .mockResolvedValueOnce([COMMAND])
    .mockResolvedValue([{ ...COMMAND, title: "Run faster", default_backend: "slurm" }]);
  render(<App />);
  await settle();
  expect(screen.getByText("Run")).toBeTruthy();
  await settle(5000);
  expect(screen.getByText("Run faster")).toBeTruthy();
  expect(useStore.getState().commands[0]?.default_backend).toBe("slurm");
});

test("a poll with the same commands leaves the store alone", async () => {
  vi.mocked(api.fetchMeta).mockResolvedValue(UNBRANDED);
  vi.mocked(api.fetchCommands).mockImplementation(() =>
    Promise.resolve([{ ...COMMAND }]),
  );
  render(<App />);
  await settle();
  const first = useStore.getState().commands;
  expect(first).toHaveLength(1);
  await settle(5000);
  expect(api.fetchCommands).toHaveBeenCalledTimes(2);
  expect(useStore.getState().commands).toBe(first);
});
