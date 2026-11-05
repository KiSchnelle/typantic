// Fake timers for a test file: every test starts on them and ends unmounted.

import { act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

export function fakeTimersPerTest(): void {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });
}

// Runs the timers due within `ms`, and lets what they fetched land and render.
export async function settle(ms = 0): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}
