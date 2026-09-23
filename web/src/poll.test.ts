// Polling is a chain of timeouts, not an interval: a slow response is never
// overlapped by the next request, so a sluggish server is not piled onto.

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { startPolling } from "./poll.ts";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test("the next poll waits for the previous one to settle", async () => {
  let settle: () => void = () => undefined;
  const load = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        settle = resolve;
      }),
  );
  const stop = startPolling(load, 1000);
  expect(load).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10_000); // a slow answer: still just one call
  expect(load).toHaveBeenCalledTimes(1);
  settle();
  await vi.advanceTimersByTimeAsync(1000);
  expect(load).toHaveBeenCalledTimes(2);
  stop();
});

test("a failed poll is retried on schedule", async () => {
  const load = vi.fn(() => Promise.reject(new Error("503")));
  const stop = startPolling(load, 1000);
  await vi.advanceTimersByTimeAsync(2000);
  expect(load).toHaveBeenCalledTimes(3);
  stop();
});

test("stopping ends the chain, even with a poll in flight", async () => {
  let settle: () => void = () => undefined;
  const load = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        settle = resolve;
      }),
  );
  const stop = startPolling(load, 1000);
  stop();
  settle();
  await vi.advanceTimersByTimeAsync(5000);
  expect(load).toHaveBeenCalledTimes(1);
});
