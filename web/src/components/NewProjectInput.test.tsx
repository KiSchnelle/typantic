import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as api from "../api.ts";
import { NewProjectInput } from "./NewProjectInput.tsx";

vi.mock("../api.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api.ts")>()),
  createProject: vi.fn(),
}));

afterEach(cleanup);

test("a rejected create shows the server's message, without JavaScript's prefix", async () => {
  vi.mocked(api.createProject).mockRejectedValue(new Error("409 dup"));
  render(<NewProjectInput onCreated={vi.fn()} onCancel={vi.fn()} />);
  fireEvent.change(screen.getByPlaceholderText("New project name"), {
    target: { value: "taken" },
  });
  fireEvent.click(screen.getByText("Create"));
  expect((await screen.findByText(/409 dup/)).textContent).toBe("409 dup");
});
