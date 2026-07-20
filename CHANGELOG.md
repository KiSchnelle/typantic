# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The web dashboard now mirrors the `--title` brand into the browser tab title;
  previously the tab always read "typantic web" regardless of `--title`.

## [0.5.1] - 2026-07-17

### Added

- `set`, `frozenset` and variadic `tuple[X, ...]` fields are now supported: they
  map to a repeatable flag and Pydantic coerces the collected values back to the
  declared type. Previously any such field raised `RuntimeError: Type not yet
  supported` when the command ran — including the `tags: set[str]` field in the
  README's own config-file example.
- `ProcessBackend`, `SchedulerBackend`, `SchedulerError` and `SchedulerParams`
  are re-exported from `typantic.web`. They are the documented subclassing
  points for a custom backend, but only the concrete backends were exported.

### Fixed

- **Aliased fields no longer discard the value you pass.** Pydantic populates by
  alias, not by field name, so `Field(alias=...)` / `validation_alias` /
  `alias_generator` (e.g. `to_camel`) silently dropped the CLI value and used the
  default instead — `--threshold 0.9` exited 0 with `threshold` still `0.5`. A
  *required* aliased field was worse: the flag was advertised, passing it still
  failed as missing, and the command could not be run at all. The flag still
  follows the field name; the value is now submitted under the alias. This also
  applies to `--generate-config`, which wrote a template that `--config` could
  not load back. An `AliasChoices`/`AliasPath` validation alias cannot map onto
  one flag and is now reported at decoration time instead of failing silently.
- **A nested model's field default is no longer thrown away.** With
  `db: Database = Database(host="prod")`, the CLI substituted `Database`'s own
  field defaults — submitting (and advertising in `--help`) `host="localhost"`.
  The outer default now seeds the flattened options at every depth.
- `SecretBytes` fields and optional secrets (`SecretStr | None`) no longer crash
  the app at import with `RuntimeError: Type not yet supported`. `SecretBytes` is
  a documented feature that could never have worked; the optional form failed
  because the secret check did not look inside a `T | None` union.
- A `default_factory` that takes the validated data (Pydantic 2.10+) no longer
  raises `TypeError` on every invocation. Click calls a default with no
  arguments, so such a factory is now left for Pydantic to run.
- `cli_short`/`cli_name` on a boolean no longer deletes its `--no-x` switch,
  which left a field defaulting to `True` impossible to turn off.
- Two fields declaring the same flag through `cli_name`/`cli_short` are now
  reported at decoration time. Click keeps only the last, so the other field
  silently stopped being settable.
- Integer `ge`/`le` bounds keep their precision. Above 2**53 a float cannot hold
  the bound exactly, and rounding it could reject the only valid value.
- `--config` with a missing or malformed file now reports a parameter error
  instead of printing a raw `FileNotFoundError`/`ValueError` traceback.
- `--generate-config` on a self-referential model no longer recurses until the
  stack runs out.
- Model annotations are read as Pydantic resolved them, so a model defined in a
  local scope under `from __future__ import annotations` no longer raises
  `NameError` for a class Pydantic itself handles.

#### `typantic[web]`

- **The live log no longer stalls the whole dashboard.** The log-tail WebSocket
  was the only async path in the server and did its work inline — including a
  scheduler poll that shells out to `sacct`/`qstat` — freezing every other
  request for every user while it ran. It now runs off the event loop.
- **A large job log no longer loads whole into memory.** The tail read to EOF and
  sent one frame, so a 200 MB training log cost ~400 MB of RSS on the server (and
  the same again through the browser). It now streams in bounded chunks, with a
  decoder that no longer corrupts a UTF-8 character split across a chunk.
- **A failed launch no longer leaves a job running untracked.** The process was
  spawned before the record was saved, so an unknown `project_id` (a foreign key)
  failed the insert *after* the job had started — leaving a live process with no
  row, no job dir to find it by, and a 500 for the caller. Everything that can be
  rejected is now rejected first, and a backend that fails mid-launch no longer
  leaves an orphaned job folder.
- **A broken scheduler is no longer reported as a queued job.** `poll` ignored the
  query's exit status, so a dead cluster (or a missing `sacct`) read as "not in
  the queue" and the job sat QUEUED forever; a missing binary or a timeout raised
  straight out of the jobs list as a 500. Slurm also reported `exit_code=0` for a
  *running* job, which the dashboard rendered as "exit 0".
- Cancelling a job that has just finished keeps its real outcome instead of
  recording it CANCELLED forever, and a job cancelled outside the dashboard now
  gets a finish time.
- A non-ASCII `?token=` returns 401 rather than 500 (`compare_digest` rejects
  non-ASCII `str` outright).
- `/api/fs` and `/api/fs/mkdir` return a listing / 400 for a `~unknownuser` path
  instead of 500 — the fallback the endpoint already documented.
- Job search treats `_` and `%` literally. They are `LIKE` wildcards, so a search
  for `job_1` also matched `job11`, and `%` matched everything.
- `query_jobs` honours `offset` when no `limit` is given (it was silently ignored).
- The PBS backend runs the job in its job folder, as the Slurm backend and the
  gallery docs already assumed; PBS starts in `$HOME`, so relative output landed
  there and the gallery found nothing.
- Launched jobs get `stdin=/dev/null` rather than inheriting the server's stdin.
- `add_endpoint` awaits an `async def` handler (it returned the un-awaited
  coroutine, which FastAPI could not serialise), and its `name` argument is no
  longer ignored when `path` is given.
- Invalid `backend_options` on a restart return 422 rather than 500, and a
  rejected restart no longer overwrites the job's stored settings first.
- Two apps registering the same `app/command` key are no longer both listed while
  every lookup resolved to one of them.
- Transparent PNGs thumbnail onto white instead of black, and EXIF orientation is
  applied — the grid disagreed with the full-size image it links to.
- The dashboard URL percent-encodes an explicit `--token` and brackets an IPv6
  host; `--host ::1` also binds correctly now.
- The dashboard reconnects its log stream, shows an error when a job action
  fails, and no longer lets a slow response overwrite a newer command's form or
  directory listing. The connection indicator can go red again once green.
- A long log path no longer makes the job detail page scroll sideways. The path
  in the log toolbar is a flex item, whose `min-width` defaults to its content
  width, so the truncation never engaged and the row stretched the page past the
  viewport (cutting off the last column of the output-image grid). The path now
  shrinks and ellipsises, with the full path still on hover. Only reachable with
  a jobs dir deep enough to overflow, which is why the default `~/.typantic/jobs`
  never showed it.
- Creating a project from the **Projects** tab now surfaces an error when it
  fails. The Projects and Launch screens carried drifting copies of the same
  new-project input, and the Projects copy swallowed the rejection, so a failed
  create silently did nothing. Both screens now share one input that reports the
  error inline.

### Changed

- The `LICENSE` file now ships in the wheel and sdist. Only the `License-Expression`
  metadata was included, while MIT asks that the notice travel with the code.
- `LaunchPreview.script` is always a string. It was typed as optional, but every
  backend renders one, so the null case (and the UI's guard for it) never existed.
- The coverage config no longer excludes `if TYPE_CHECKING:`, `raise
  NotImplementedError` or `@abstractmethod`. The first two matched nothing, and
  the third was redundant — dropping it counts 12 more statements in
  `scheduler.py`, so the 100% gate is now strictly stronger.
- `make check` lints the whole tree, matching CI. It linted only `src`/`tests`,
  so an `examples/` violation passed locally and failed in CI.
- The **Projects** tab no longer refetches the project list on its own 3-second
  timer. The app already polls it for the whole session, so the tab was issuing a
  redundant second request; it now refreshes eagerly only after a create or
  delete, where immediacy matters.
- Internal reorganization, no behavior or public-API change: the directory-picker
  filesystem logic moved out of the HTTP layer into its own
  `typantic.web.filesystem` module (mirroring `gallery`); config-file key
  validation moved next to the loader in `typantic._config_file`; the dashboard's
  log-socket frame parsing moved into its API client; and the shared new-project
  input became one component. Pure code-location changes.

### Removed

- `Launcher.refresh_all`, `Launcher.backend_keys` and `command_catalog`. Each was
  a vestigial earlier version of something already in use (`query`/`get`,
  `backends_meta`, and the dashboard's own grouping), called by nothing but its
  own test. `refresh_all` also polled every job ever launched, which the paged
  query exists to avoid.

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
- Raised minimum dependency versions to track what we test against: `typer`
  ≥ 0.27 (runtime) and, for development/build only, `ruff` ≥ 0.15.22,
  `mypy` ≥ 2.3.0, and `uv_build` ≥ 0.11.29.

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