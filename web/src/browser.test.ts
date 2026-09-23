// Download and copy, in the browsers a dashboard is actually opened in.

import { afterEach, expect, test, vi } from "vitest";
import { copyText, downloadText } from "./browser.ts";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test("a download's object URL outlives the click that starts it", () => {
  // Revoked synchronously after click(), the URL could be gone before the
  // browser began the download, which then failed or saved nothing.
  vi.useFakeTimers();
  const revoke = vi.fn();
  URL.createObjectURL = vi.fn(() => "blob:log");
  URL.revokeObjectURL = revoke;
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    () => undefined,
  );
  downloadText("job.log", "line\n");
  expect(click).toHaveBeenCalledTimes(1);
  expect(revoke).not.toHaveBeenCalled();
  vi.runAllTimers();
  expect(revoke).toHaveBeenCalledWith("blob:log");
});

test("copy uses the clipboard API where there is one", async () => {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  expect(await copyText("hello")).toBe(true);
  expect(writeText).toHaveBeenCalledWith("hello");
});

test("copy still works where the clipboard API is missing", async () => {
  // navigator.clipboard exists only in a secure context; a dashboard opened
  // over plain http by host name has none, and Copy threw a TypeError.
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
  const execCommand = vi.fn(() => true);
  document.execCommand = execCommand;
  expect(await copyText("hello")).toBe(true);
  expect(execCommand).toHaveBeenCalledWith("copy");
  expect(document.querySelector("textarea")).toBeNull(); // cleaned up
});

test("copy reports a failure instead of throwing", async () => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: () => Promise.reject(new Error("denied")) },
    configurable: true,
  });
  document.execCommand = vi.fn(() => {
    throw new Error("unsupported");
  });
  expect(await copyText("hello")).toBe(false);
});
