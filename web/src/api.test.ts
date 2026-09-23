// The log socket's frame envelope: output arrives as {"log": ...}, the end of a
// job as {"end": ...}, so no log text can ever be mistaken for the end signal.

import { expect, test } from "vitest";
import { isEndFrame, logChunk } from "./api.ts";

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
