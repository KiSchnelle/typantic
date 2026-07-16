import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Form from "@rjsf/core";
import type { IChangeEvent } from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import { ArrowLeft, Eye, FolderPlus, Rocket, RotateCcw, X } from "lucide-react";
import {
  createProject,
  fetchProjects,
  fetchSchema,
  launchJob,
  previewLaunch,
  restartJob,
} from "../api.ts";
import { useStore } from "../store.ts";
import type { CommandMeta, JsonSchema, LaunchPreview } from "../types.ts";
import { Button, Card } from "./ui.tsx";
import { CheckboxWidget, templates } from "./rjsfTemplates.tsx";
import { PathWidget } from "./PathWidget.tsx";
import { Section } from "./Section.tsx";

const WIDGETS = { path: PathWidget, CheckboxWidget };

type Schema = Record<string, unknown>;

// A Pydantic path field: plain Path (`format: "path"`), FilePath/DirectoryPath
// (`file-path`/`directory-path`), or one tagged with a `picker` hint.
function isPathField(s: Schema): boolean {
  return (
    s.format === "path" ||
    s.format === "file-path" ||
    s.format === "directory-path" ||
    s.picker === "file" ||
    s.picker === "dir"
  );
}

function pickerMode(s: Schema): "file" | "dir" | "any" {
  if (s.picker === "file" || s.format === "file-path") return "file";
  if (s.picker === "dir" || s.format === "directory-path") return "dir";
  return "any";
}

function pathWidgetUi(s: Schema): Schema {
  return { "ui:widget": "path", "ui:options": { mode: pickerMode(s) } };
}

// Attach the path picker to every path field, including `list[Path]` array items.
function pathUiSchema(schema: Schema): Schema {
  const ui: Schema = {};
  const props = schema.properties as Record<string, Schema> | undefined;
  if (!props) return ui;
  for (const [key, s] of Object.entries(props)) {
    const items = s.items as Schema | undefined;
    if (isPathField(s)) {
      ui[key] = pathWidgetUi(s);
    } else if (s.type === "array" && items && isPathField(items)) {
      ui[key] = { items: pathWidgetUi(items) };
    } else if (s.type === "object" && s.properties) {
      const nested = pathUiSchema(s);
      if (Object.keys(nested).length > 0) ui[key] = nested;
    }
  }
  return ui;
}

// Drop the schema's root title (class name) and description (docstring) so RJSF
// doesn't repeat what's already shown above the form.
function withoutRootTitle(schema: JsonSchema): JsonSchema {
  const rest: Record<string, unknown> = { ...schema };
  delete rest.title;
  delete rest.description;
  return rest;
}

function Catalog(): ReactNode {
  const { commands, selectCommand } = useStore();
  const byApp = new Map<string, CommandMeta[]>();
  for (const c of commands) {
    const list = byApp.get(c.app) ?? [];
    list.push(c);
    byApp.set(c.app, list);
  }
  if (commands.length === 0) {
    return (
      <p className="text-slate-500">
        No commands discovered. Install an app that registers commands under the{" "}
        <code>typantic.web_commands</code> entry-point group.
      </p>
    );
  }
  return (
    <div className="space-y-6">
      {[...byApp.entries()].map(([app, cmds]) => (
        <section key={app}>
          <h2 className="mb-2 font-mono text-sm text-cyan-400">{app}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {cmds.map((c) => (
              <Card key={c.key} onClick={() => selectCommand(c.key)}>
                <div className="font-semibold text-slate-100">{c.title}</div>
                <p className="mt-1 text-sm text-slate-400">{c.description}</p>
                <div className="mt-2 text-xs text-slate-500">
                  default: {c.default_backend}
                </div>
              </Card>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export default function Launch(): ReactNode {
  const {
    commands,
    backends,
    projects,
    setProjects,
    selectedCommandKey,
    selectCommand,
    openJob,
    prefill,
    clearPrefill,
    restartJobId,
  } = useStore();
  const meta = commands.find((c) => c.key === selectedCommandKey) ?? null;

  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [formData, setFormData] = useState<unknown>({});
  const [backend, setBackend] = useState<string>("local");
  const [backendOptions, setBackendOptions] = useState<Record<string, unknown>>({});
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<LaunchPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const backendKeys = backends.length ? backends.map((b) => b.key) : [backend];
  const optionsSchema =
    backends.find((b) => b.key === backend)?.options_schema ?? null;

  const formSchema = useMemo(
    () => (schema ? withoutRootTitle(schema) : null),
    [schema],
  );

  // Path pickers + a "required first, then the rest" order.
  const uiSchema = useMemo(() => {
    if (!schema) return {};
    const ui = pathUiSchema(schema);
    const required = (schema.required as string[] | undefined) ?? [];
    if (required.length > 0) ui["ui:order"] = [...required, "*"];
    return ui;
  }, [schema]);

  const optionsUi = useMemo(
    () => (optionsSchema ? pathUiSchema(optionsSchema) : {}),
    [optionsSchema],
  );

  // Key the fetch on the stable command key, NOT the meta object.
  useEffect(() => {
    if (!selectedCommandKey) return;
    const current = useStore
      .getState()
      .commands.find((c) => c.key === selectedCommandKey);
    setSchema(null);
    setError(null);
    setFormData({});
    setName("");
    setProjectId(null);
    setBackendOptions({});
    if (current) setBackend(current.default_backend);
    fetchSchema(selectedCommandKey)
      .then(setSchema)
      .catch((e: unknown) => setError(String(e)));
  }, [selectedCommandKey]);

  // A "Clone" action stashed the source job's request; fill it in, then consume.
  useEffect(() => {
    if (!prefill || prefill.command_key !== selectedCommandKey) return;
    setFormData(prefill.values);
    setBackend(prefill.backend);
    setBackendOptions(prefill.backend_options ?? {});
    setName(prefill.name ?? "");
    setProjectId(prefill.project_id ?? null);
    clearPrefill();
  }, [prefill, selectedCommandKey, clearPrefill]);

  if (!meta) return <Catalog />;

  const buildRequest = (values: Record<string, unknown>) => ({
    command_key: meta.key,
    backend,
    name: name.trim() || null,
    project_id: projectId,
    values,
    backend_options: backendOptions,
  });

  const submit = async (data: IChangeEvent) => {
    setBusy(true);
    setError(null);
    const request = buildRequest((data.formData ?? {}) as Record<string, unknown>);
    try {
      const record = restartJobId
        ? await restartJob(restartJobId, request)
        : await launchJob(request);
      openJob(record.id);
    } catch (e: unknown) {
      setError(String(e));
      setBusy(false);
    }
  };

  const doPreview = async () => {
    setPreviewing(true);
    setError(null);
    try {
      setPreview(
        await previewLaunch(
          buildRequest((formData ?? {}) as Record<string, unknown>),
        ),
      );
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setPreviewing(false);
    }
  };

  const createNewProject = () => {
    const name = newProjectName.trim();
    if (!name) return;
    void createProject(name)
      .then((p) => {
        setProjectId(p.id);
        setCreatingProject(false);
        setNewProjectName("");
        void fetchProjects().then(setProjects);
      })
      .catch((e: unknown) => setError(String(e)));
  };

  return (
    <div className="max-w-3xl">
      <button
        onClick={() => selectCommand(null)}
        className="mb-4 flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
      >
        <ArrowLeft size={16} /> All commands
      </button>
      <h1 className="text-xl font-semibold text-slate-100">{meta.title}</h1>
      <p className="mb-4 text-sm text-slate-400">{meta.description}</p>

      {restartJobId && (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-amber-800 bg-amber-950/40 p-3 text-sm text-amber-200">
          <RotateCcw size={16} className="mt-0.5 shrink-0" />
          <span>
            Restarting this job with edited settings. Submitting re-runs the{" "}
            <span className="font-semibold">same job</span> in place. To keep the
            old run instead, use Clone.
          </span>
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-md border border-red-800 bg-red-950/40 p-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {!schema && !error && <p className="text-slate-500">Loading form…</p>}

      {schema && (
        <Form
          key={meta.key}
          className="rjsf"
          schema={formSchema ?? schema}
          validator={validator}
          templates={templates}
          widgets={WIDGETS}
          uiSchema={uiSchema}
          formData={formData}
          onChange={(e: IChangeEvent) => setFormData(e.formData)}
          onSubmit={submit}
        >
          <Section title="Submission">
            <label className="mb-4 block text-sm">
              <span className="mb-1 block font-semibold text-slate-400">
                Job name{" "}
                <span className="font-normal text-slate-500">(optional)</span>
              </span>
              <input
                className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-100"
                value={name}
                placeholder="e.g. first test run"
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <label className="mb-4 block text-sm">
              <span className="mb-1 block font-semibold text-slate-400">
                Project{" "}
                <span className="font-normal text-slate-500">(optional)</span>
              </span>
              {creatingProject ? (
                <div className="flex gap-2">
                  <input
                    autoFocus
                    className="flex-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-100"
                    placeholder="New project name"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        createNewProject();
                      } else if (e.key === "Escape") {
                        setCreatingProject(false);
                      }
                    }}
                  />
                  <button
                    type="button"
                    className="rjsf-add-btn"
                    disabled={newProjectName.trim() === ""}
                    onClick={createNewProject}
                  >
                    Create
                  </button>
                  <button
                    type="button"
                    className="rjsf-icon-btn"
                    aria-label="Cancel new project"
                    onClick={() => setCreatingProject(false)}
                  >
                    <X size={15} />
                  </button>
                </div>
              ) : (
                <div className="flex gap-2">
                  <select
                    className="flex-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-100"
                    value={projectId ?? ""}
                    onChange={(e) => setProjectId(e.target.value || null)}
                  >
                    <option value="">No project</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="rjsf-icon-btn"
                    aria-label="New project"
                    title="New project"
                    onClick={() => {
                      setNewProjectName("");
                      setCreatingProject(true);
                    }}
                  >
                    <FolderPlus size={15} />
                  </button>
                </div>
              )}
            </label>

            <fieldset className="mb-4 rounded-lg border border-slate-800 p-3">
              <legend className="px-1 text-sm font-bold text-slate-300">
                Backend
              </legend>
              <div className="flex flex-wrap gap-4">
                {backendKeys.map((b) => (
                  <label key={b} className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      checked={backend === b}
                      onChange={() => {
                        setBackend(b);
                        setBackendOptions({});
                      }}
                    />
                    <span className="capitalize">{b}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            {optionsSchema && (
              <fieldset className="mb-4 rounded-lg border border-slate-800 p-3">
                <legend className="px-1 text-sm font-bold text-slate-300 capitalize">
                  {backend} options
                </legend>
                <Form
                  tagName="div"
                  className="rjsf"
                  schema={withoutRootTitle(optionsSchema)}
                  validator={validator}
                  templates={templates}
                  widgets={WIDGETS}
                  uiSchema={optionsUi}
                  formData={backendOptions}
                  onChange={(e: IChangeEvent) => setBackendOptions(e.formData ?? {})}
                >
                  <></>
                </Form>
              </fieldset>
            )}
          </Section>

          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" disabled={busy}>
              <span className="flex items-center gap-2">
                {restartJobId ? (
                  <>
                    <RotateCcw size={16} /> {busy ? "Restarting…" : "Restart job"}
                  </>
                ) : (
                  <>
                    <Rocket size={16} /> {busy ? "Launching…" : "Launch"}
                  </>
                )}
              </span>
            </Button>
            <Button type="button" onClick={doPreview} disabled={previewing}>
              <span className="flex items-center gap-2">
                <Eye size={16} /> {previewing ? "…" : "Preview"}
              </span>
            </Button>
          </div>
        </Form>
      )}

      {preview && (
        <div className="modal-overlay" onClick={() => setPreview(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <Eye size={16} className="text-cyan-400" />
              <span className="modal-cwd">Submission preview — {backend}</span>
              <button
                type="button"
                aria-label="Close"
                className="rjsf-icon-btn ml-auto"
                onClick={() => setPreview(null)}
              >
                <ArrowLeft size={14} />
              </button>
            </header>
            <div className="modal-body">
              <div className="preview-label">Config (submit_config.json)</div>
              <pre className="preview-pre">{preview.config}</pre>
              {preview.script && (
                <>
                  <div className="preview-label">Command / submit script</div>
                  <pre className="preview-pre">{preview.script}</pre>
                </>
              )}
            </div>
            <footer className="modal-foot">
              <span className="text-xs text-slate-500">
                This is exactly what would be submitted.
              </span>
              <button
                type="button"
                className="rjsf-add-btn"
                onClick={() => setPreview(null)}
              >
                Close
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
