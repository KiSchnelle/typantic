// The log view shows a job's log once, whatever its socket goes through.

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { REQUEST, job } from "../__fixtures__/jobs.ts";
import JobDetail from "./JobDetail.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  fetchJob: vi.fn(),
  fetchJobRequest: vi.fn(),
  fetchImages: vi.fn(),
  openLogSocket: vi.fn(),
}));

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  close = vi.fn();

  deliver(frame: object): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

let sockets: FakeSocket[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  sockets = [];
  vi.mocked(api.fetchJob).mockResolvedValue(job("j1"));
  vi.mocked(api.fetchJobRequest).mockResolvedValue(REQUEST);
  vi.mocked(api.fetchImages).mockResolvedValue({ images: [], truncated: false });
  vi.mocked(api.openLogSocket).mockImplementation(() => {
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket as unknown as WebSocket;
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function mount(): Promise<void> {
  render(<JobDetail id="j1" />);
  await act(() => vi.advanceTimersByTimeAsync(0)); // let the first fetches land
}

function shownLog(): string {
  return screen.getByLabelText("Job log").textContent ?? "";
}

test("a reconnect shows the log once, not twice", async () => {
  // The server replays the log from its start on every connection, and the
  // view appended the replay to what it already showed.
  await mount();
  act(() => {
    sockets[0].onopen?.();
    sockets[0].deliver({ log: "line 1\n" });
  });
  expect(shownLog()).toBe("line 1\n");
  act(() => sockets[0].onclose?.()); // the server restarted
  await act(() => vi.advanceTimersByTimeAsync(1000));
  act(() => {
    sockets[1].onopen?.();
    sockets[1].deliver({ log: "line 1\n" });
  });
  expect(shownLog()).toBe("line 1\n");
});

test("a reset frame clears the log before the new one streams in", async () => {
  await mount();
  act(() => {
    sockets[0].onopen?.();
    sockets[0].deliver({ log: "old run\n" });
    sockets[0].deliver({ reset: true });
    sockets[0].deliver({ log: "new run\n" });
  });
  expect(shownLog()).toBe("new run\n");
});

test("the header names the app version the job ran with", async () => {
  vi.mocked(api.fetchJob).mockResolvedValue(job("j1", { app_version: "0.2.0" }));
  await mount();
  expect(screen.getByText("v0.2.0").getAttribute("title")).toContain("app version");
});
