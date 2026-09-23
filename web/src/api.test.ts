// The log socket's frame envelope: output arrives as {"log": ...}, the end of a
// job as {"end": ...}, so no log text can ever be mistaken for the end signal.

import { expect, test, vi } from "vitest";
import { deleteJob, fetchJob, isEndFrame, isResetFrame, logChunk } from "./api.ts";

test("a log frame yields its text and is not the end", () => {
  const frame = JSON.stringify({ log: "step 1\n" });
  expect(logChunk(frame)).toBe("step 1\n");
  expect(isEndFrame(frame)).toBe(false);
});

test("log text that looks like the end frame is still just log text", () => {
  const frame = JSON.stringify({ log: '{"end": {"status": "done"}}' });
  expect(logChunk(frame)).toBe('{"end": {"status": "done"}}');
  expect(isEndFrame(frame)).toBe(false);
});

test("the end frame carries no log text", () => {
  const frame = JSON.stringify({ end: { status: "done" } });
  expect(logChunk(frame)).toBe("");
  expect(isEndFrame(frame)).toBe(true);
});

test("a malformed frame is neither text nor the end", () => {
  expect(logChunk("not json")).toBe("");
  expect(isEndFrame("not json")).toBe(false);
});

test("a reset frame says the log starts over, and carries no text", () => {
  // Sent when the job's log was replaced (a restart in place) or truncated.
  const frame = JSON.stringify({ reset: true });
  expect(isResetFrame(frame)).toBe(true);
  expect(logChunk(frame)).toBe("");
  expect(isResetFrame(JSON.stringify({ log: "reset" }))).toBe(false);
  expect(isResetFrame("not json")).toBe(false);
});

test("an API error reads as its detail, not the JSON around it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "Job j1 is deleted, but not its folder" }), {
          status: 409,
        }),
      ),
    ),
  );
  await expect(deleteJob("j1")).rejects.toThrow("409 Job j1 is deleted, but not its folder");
  vi.unstubAllGlobals();
});

test("an error body that is not JSON is shown as it is", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response("Bad Gateway", { status: 502 }))),
  );
  await expect(fetchJob("j1")).rejects.toThrow("502 Bad Gateway");
  vi.unstubAllGlobals();
});
