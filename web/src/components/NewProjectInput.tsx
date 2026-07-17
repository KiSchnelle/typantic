import { useState } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { createProject } from "../api.ts";
import type { Project } from "../types.ts";
import { cn } from "./ui.tsx";

// The inline "type a name → Create" widget, shared by the Launch form and the
// Projects tab. It owns its own name + error state so a failed create is always
// surfaced — one of the two former copies silently swallowed the rejection.
// `preventDefault` on Enter is harmless standalone and required inside the RJSF
// <form> on the Launch screen (else Enter submits the launch form).
export function NewProjectInput({
  onCreated,
  onCancel,
  inputClassName = "flex-1",
}: {
  onCreated: (project: Project) => void;
  onCancel: () => void;
  inputClassName?: string;
}): ReactNode {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    void createProject(trimmed)
      .then(onCreated)
      .catch((e: unknown) => setError(String(e)));
  };

  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          autoFocus
          className={cn(
            "rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100",
            inputClassName,
          )}
          placeholder="New project name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              create();
            } else if (e.key === "Escape") {
              onCancel();
            }
          }}
        />
        <button
          type="button"
          className="rjsf-add-btn"
          disabled={name.trim() === ""}
          onClick={create}
        >
          Create
        </button>
        <button
          type="button"
          aria-label="Cancel"
          className="rjsf-icon-btn"
          onClick={onCancel}
        >
          <X size={15} />
        </button>
      </div>
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
}
