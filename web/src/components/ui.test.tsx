import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { relativeTime } from "./ui.tsx";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-23T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("a time slightly ahead of this clock reads as just now", () => {
  // The server's clock a few seconds ahead of the browser's showed "-5s ago".
  expect(relativeTime("2026-09-23T12:00:05Z")).toBe("just now");
});

test("past times read in the largest whole unit", () => {
  expect(relativeTime("2026-09-23T11:59:30Z")).toBe("30s ago");
  expect(relativeTime("2026-09-23T11:15:00Z")).toBe("45m ago");
  expect(relativeTime("2026-09-23T09:00:00Z")).toBe("3h ago");
  expect(relativeTime("2026-09-20T12:00:00Z")).toBe("3d ago");
  expect(relativeTime(null)).toBe("—");
});
