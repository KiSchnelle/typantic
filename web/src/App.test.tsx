import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "./api.ts";
import App from "./App.tsx";
import { useStore } from "./store.ts";
import type { ApiMeta } from "./types.ts";

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

beforeEach(() => {
  vi.useFakeTimers();
  useStore.setState(initial, true);
  document.head.innerHTML = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">';
  vi.mocked(api.fetchCommands).mockResolvedValue([]);
  vi.mocked(api.fetchProjects).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  document.documentElement.removeAttribute("style");
});

async function settle(ms = 0): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

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
