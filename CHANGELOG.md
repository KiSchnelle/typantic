# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-07-16

### Added

- **Optional `[web]` extra — a generic job launcher, dashboard, and web-form
  bridge** (`pip install 'typantic[web]'`), under `typantic.web` plus a
  `typantic web serve` command. The base `import typantic` never imports FastAPI.
  - `add_endpoint(app, Model, handler)` — the FastAPI mirror of `add_command`: a
    POST endpoint that validates the body into the model and calls the handler
    (422 on invalid input), plus a `GET {path}/schema` route with the form-ready
    JSON Schema.
  - A **per-user launcher + dashboard** that discovers commands via the
    `typantic.web_commands` entry-point group, renders a form from each command's
    `--schema`, launches `<app> <cmd> --config` as a tracked job, tails its log
    over a WebSocket, and shows a job's output images as thumbnails. It shells
    out (never imports app code), so heavy app dependencies stay out of the web
    process.
  - **Pluggable backends** via the `typantic.web_backends` entry-point group:
    `local`, `ssh`, `slurm`, `pbs`, `docker`, `podman`, and `apptainer` ship
    built in; a third-party backend is a pure registry addition.
  - **SQLite-backed history with projects** (stdlib only): file a job under a
    project; browse history grouped by project plus ungrouped singles; and
    **search, filter** (status / app / backend / project), **sort, and paginate**
    the jobs list. Deleting a project also deletes its jobs — their logs,
    configs, and output — cancelling any still running.
  - Runs as the invoking Unix user on a free ephemeral port behind a random
    token (the Jupyter pattern); the brand is configurable with `--title`.
- `--schema` flag on any `config_file`-enabled command prints the settings
  model's JSON Schema (`model_json_schema()`) to stdout and exits. This lets a
  web front-end build a form from the model by subprocessing the CLI, without
  importing the model (keeping heavy app dependencies out of the web process).
- `config_file="only"` on `pydantic_to_typer` / `add_command` registers a
  **file-only** command — only `--config` / `--generate-config`, no per-field
  flags — for settings models that cannot map onto flat flags (nested-model
  lists, `scalar | (min, max)` ranges).
- `add_command(..., help=...)` to set the command's help text explicitly
  (otherwise the handler's docstring is used).
- Test suite now runs under **branch coverage** — `pytest-cov` added as a dev
  dependency and `[tool.coverage]` configured (`branch = true` plus a shared
  `exclude_lines` set). Development-only; the published package is unaffected.
- A runnable `typantic[web]` example app (`examples/typantic_demo`) whose two
  settings models become both a Typer CLI and a web form. Repo-only (not shipped
  in the wheel).

### Changed

- An unknown key in a `--config` file is now rejected instead of silently
  dropped. Pydantic's default `extra="ignore"` meant a typo such as `wrokers: 8`
  was discarded and the field left at its default — an invisible mistake that
  could waste a long run. The file's keys are now validated against the model up
  front (recursing into nested models) and any unknown ones raise a clear error.
  Computed-field names stay allowed, so a written-back config (which serialises
  them) still round-trips; command-line flags are unaffected.
- `--help` no longer freezes a `default_factory` sample. A factory-defaulted
  option previously showed a single evaluation captured at decoration time (e.g.
  `[default: (/…/run_<frozen-timestamp>)]`), which misled for time/identity
  factories since each invocation recomputes a different value. It now shows
  `[default: (computed at runtime)]`. The runtime default is unchanged (the
  factory is still re-evaluated on every invocation). This also removes an
  edge-case crash where a validated-data `default_factory` was called with no
  arguments to build the sample.
- Generated templates no longer freeze `default_factory` values. A
  factory-defaulted field now renders as a `<DEFAULT: computed at runtime>`
  sentinel that `load_config_file` strips, so a host/time-sensitive default (a
  timestamped output folder, a CPU count) is recomputed fresh on load instead of
  replaying a stale value baked in on the generating host. Static defaults are
  unchanged.
- Bumped dev dependency `mypy` to 2.3.0. Development-only; the published package
  is unaffected.

### Fixed

- A flattened-name collision (a nested field such as `db.host` flattening to
  `db_host` while a sibling field is literally named `db_host`) now raises a clear
  typantic error naming both fields and the resulting flag, instead of an opaque
  `inspect.Signature` "duplicate parameter" `ValueError` at decoration time.
- Numeric `ge` / `le` bounds now map onto Typer's `min` / `max` (range in
  `--help`, rejection of out-of-range input) for optional numeric fields too
  (`int | None` / `float | None`). Previously the optional wrapper left the base
  type as a union, so the bounds were silently dropped at the CLI layer (Pydantic
  still enforced them on the parsed value).
- Generated templates now render a required list of non-model values (e.g.
  `list[Path]` / `list[str]`) as a single-element example list
  (`['<REQUIRED: ...>']`) instead of a bare scalar placeholder, so editing the
  template in the shape shown reloads as a valid list instead of raising
  `ValidationError: Input should be a valid list`.
- Passing both `--config` and `--generate-config` now errors (mutually
  exclusive) instead of silently generating the template and skipping the run.
  Applies to both `config_file=True` and `config_file="only"`.

## [0.4.1] - 2026-06-23

### Changed

- Packaging metadata only (no code changes from 0.4.0): marked **Beta** (was
  Alpha), expanded the PyPI classifiers (`Environment :: Console`,
  `Framework :: Pydantic` / `:: 2`, `Topic` and `Operating System` entries) and
  keywords, and clarified the README intro.

## [0.4.0] - 2026-06-22

### Added

- Opt-in config-file support via `config_file=True` on `pydantic_to_typer` /
  `add_command`. It injects two options: `--generate-config PATH` writes an
  editable default template (required fields become `<REQUIRED: ...>`
  placeholders; nested models and lists of models are expanded) and exits without
  running; `--config PATH` loads settings from a YAML/JSON file as the base, with
  any explicitly-passed flags overriding it. To let `--config` supply them,
  required fields are relaxed to optional at the Typer layer and re-validated by
  Pydantic after the merge.
- Public helpers `build_config_template`, `write_config_template`, and
  `load_config_file` for templating and reading settings files directly. The
  template serialiser uses Pydantic's JSON serialisation, so nested models, sets,
  datetimes, paths and enums round-trip, and `default_factory` callables that take
  the validated-data dict are handled.
- Python 3.15 added to the supported versions and the CI test matrix.

### Changed

- `add_command` is now generic over the model type, so the `handler` callback is
  typed against the concrete model class instead of the base `BaseModel`. Type
  checkers now infer the precise model type passed to the handler.
- Minimum Pydantic bumped to `>=2.10` (for `default_factory` validated-data
  introspection); added a `PyYAML>=6.0` dependency for config-file I/O.

## [0.3.0] - 2026-06-15

### Added

- Nested `BaseModel` fields are now flattened into prefixed CLI parameters
  (e.g. a `db: Database` field with a `host` field becomes `--db-host`). Values
  are re-nested before the model is constructed, so nested validators and
  defaults still apply.
- `SecretStr` / `SecretBytes` fields map to hidden input, with a secure prompt
  when the field is required.
- Numeric constraints `ge` / `le` map onto Typer's `min` / `max`, so bounds are
  validated by Typer and shown in `--help` (exclusive `gt` / `lt` are left to
  Pydantic).
- Per-field CLI hints via `Field(json_schema_extra=...)`: `cli_short` (add a
  short flag), `cli_name` (override the long flag), and `cli_envvar` (read the
  value from an environment variable).
- `add_command(app, model, handler, ...)` helper to register a model-driven
  command without decorating a stub function.

- `Literal[...]` fields are now documented and tested as CLI choices (this
  already worked via Typer; it is now a guaranteed, covered feature).

### Changed

- `Field(default_factory=...)` is now passed through to Click as a callable
  default, so it is re-evaluated on every invocation instead of once at
  decoration time. This fixes time- and identity-sensitive factories (e.g.
  `datetime.now`, `uuid4`); a sample value is still shown in `--help`.

### Fixed

- Validators that raise `ValueError` / `AssertionError` surface as Typer
  parameter errors; other exception types propagate unchanged (documented).
- The test suite is now clean under both `mypy --strict` and `pyright`
  (narrowed parameter metadata and validated-model attribute access).

## [0.2.1] - 2026-06-11

### Fixed

- Options with a `None` default now render `[default: (None)]` in `--help`
  instead of omitting the default entirely (Click skips `None` defaults), so
  optional options are visibly optional.

## [0.2.0] - 2026-06-11

### Added

- `subpanels` option for `pydantic_to_typer` (off by default): groups CLI
  options into Rich help panels. Each option is placed in the panel named by
  the `cli_panel` class attribute of the class that defines its field, so
  models composed from mixins get organised `--help` output. Fields whose
  defining class declares no `cli_panel` stay in the default options group;
  arguments are never panelled.

## [0.1.1] - 2026-06-09

### Changed

- Bumped minimum `typer` requirement to `>=0.26` (upgraded from 0.25.1 to 0.26.7).
- Bumped dev dependency `ruff` to `>=0.15.16`.

## [0.1.0] - 2026-05-19

### Added

- Initial release.
- `pydantic_to_typer` decorator that auto-generates a Typer CLI interface
  from a Pydantic model's fields, descriptions, defaults, and validators.