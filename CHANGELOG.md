# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Many more field types work as CLI flags.** Typer parses only a handful of
  types itself. Anything else used to crash Typer *while it built the command
  group*, which took down every command of the app, `--help` included. Examples:
  `Decimal`, `date`, `time`, `timedelta`, URLs, IP addresses, `ByteSize`,
  `bytes`, `Any`, `int | str`, `list[Literal[...]]`, `Sequence[str]`,
  `deque[int]`, a `NewType`, a PEP 695 `type` alias, and an optional nested
  model.
  - Any single value Pydantic can parse from text is now taken as text and
    parsed by Pydantic, and `--help` names what it takes (`<decimal>`). Lists
    and tuples of such values work item by item.
  - `NewType` and `type` aliases are handled as the type they wrap.
  - A `Model | None` field is flattened like a required nested model. Its flags
    are optional and it stays `None` unless one of them is passed; Pydantic
    checks a partially given one.
  - A type no flag can express (a `dict`, a list of lists or of models) is
    refused at decoration, naming the field and pointing at
    `config_file="only"`.

- The dashboard's thumbnail cache follows `$XDG_CACHE_HOME`, and
  `$TYPANTIC_WEB_CACHE_DIR` moves it anywhere (it was always
  `~/.cache/typantic/thumbnails`). It no longer grows forever: the server prunes
  thumbnails nobody has opened in 30 days when it starts. The cache folder is
  now private (0700), like the files in it.
- `GET /api/jobs/{id}/images` reports `truncated` when a job has more output
  images than the gallery lists. The dashboard then shows the newest and says
  so. (The response was already an object holding `images`; the field is
  additive.)

### Changed

- **Breaking: a flag you did not pass no longer reaches the model** — in either
  mode. Without `config_file`, typantic used to hand Pydantic every flag's
  Click-resolved default as though you had typed it. So `model_fields_set`
  listed every field, a validator that derives a value "when unset" never
  fired, a nested model's `default_factory` was bypassed or frozen at import
  time, and the model behaved differently the moment you switched on
  `config_file=True`. Now only values you supplied (a flag, its environment
  variable, or a prompt) are passed, and Pydantic applies its own defaults.
  **Migration:** `model_fields_set` / `model_dump(exclude_unset=True)` now hold
  only what was passed. If you relied on every field appearing there, use
  `model_dump()`. There is no switch back to the old behaviour: counting
  defaults as passed is exactly what delivered secret defaults as their mask
  (below).
- **Breaking (form schema): an optional field left untouched now submits
  `None`.** Collapsing `X | None` to `X` for form rendering let RJSF fill in a
  value nobody chose, or refuse to submit at all:
  - An optional model (`denoise: Denoise | None = None`) was sent as
    `{"strength": 0.5}`.
  - A one-value `Literal["x"] | None` was sent as `"x"`.
  - An optional discriminated union was sent as its first branch.
  - `Literal["fast", "slow"] | None`, a model with a required field, and
    `tuple[int, int] | None` could not be submitted until something was typed.

  Now:
  - An optional choice (a `Literal`, or an `Enum` via `$ref`) is a plain select
    that starts empty.
  - An optional model, fixed tuple or nested union keeps a two-way choice whose
    `None` option is selected until you pick the other.
  - Plain optional scalars and lists render exactly as before.

  This changes the shape `normalize_for_form` returns (and so `add_endpoint`'s
  `/schema` route). It was verified against RJSF 6 itself in the dashboard's
  new vitest suite.
- The dashboard's path picker takes a relative path from your home directory
  (it used the server's working directory, which you never see). It keeps a
  path as you spelled it rather than resolving symlinks: the path you pick is
  what the job's config receives, and a resolved one (`/scratch/…` turned into
  `/lustre/…`) may not exist where the job runs.
- A gallery thumbnail that cannot be rendered now answers HTTP 415, and the
  tile shows the file's name. It used to stream the full-size original into a
  384 px tile, which for a detector frame or a large plot meant hundreds of
  megabytes per tile. Clicking the tile still opens the original.
- A factory-defaulted field's flag no longer hands Click the factory. Pydantic
  runs it once per run, so it no longer also runs for `--help`, `--schema` and
  `--generate-config`, or twice per `config_file=True` run.

### Fixed

- **A `SecretStr` / `SecretBytes` default reached your function as the mask
  `'**********'`** when the flag was not passed without `config_file`. Click
  renders a secret default as its masked string, and that string was then
  treated as the value you typed. This happened at the top level and inside a
  defaulted nested model.
- With `config_file=True`, passing one flag of a defaulted nested model reset
  its other leaves to the inner class's defaults, contradicting the defaults
  `--help` showed for them. A passed flag is now written into a copy of the
  nested default instance, unless a `--config` file defines that model itself.
- With `config_file=True`, a required nested model whose own fields all have
  defaults was reported as missing when neither a flag nor the file set it. It
  now builds from those defaults, as it already did without `config_file`.
- **The config-file key check now accepts exactly the keys Pydantic accepts.** It
  used a key set of its own, and was wrong in both directions:
  - A `populate_by_name` model with an alias generator rejected the very keys
    `--schema` advertises (`maxWorkers`), so **every dashboard launch of such a
    command exited 2**.
  - A file keyed by a field's *name* on a model that accepts only its alias was
    let through, and Pydantic then dropped the value in silence.

  Such a key is now reported with the key to use (`threshold (use 'thr')`). A
  file that relied on the silent drop fails loudly instead. A flag still
  overrides the file however the file spells the field.
- The key check looks inside `Model | None` values and each item of a list of
  models (`mounts[1].destt`) instead of stopping there. A model with
  `extra="allow"` is no longer checked at all. A written-back computed field now
  reloads on an `extra="forbid"` model, because it is dropped before the model is
  built.
- **`--generate-config` wrote a secret default as its mask**, and loading the
  template read `'**********'` back as the value. A secret default is now written
  as the `<DEFAULT: computed at runtime>` sentinel, which loading strips, so the
  real default applies. A nested default instance holding a secret is treated
  the same way as a whole.
- A template built from a nested default instance failed its own reload: the
  instance was dumped by *serialization* alias, which a load rejects. Model
  instances are now written field by field under the keys a load accepts, at any
  depth, including inside lists and dicts.
- **An unedited template ran** with `'<REQUIRED: App name.>'` as the value, for
  example writing into a directory literally named after the placeholder.
  `load_config_file` (and so `--config`) now refuses a file that still holds a
  placeholder, naming each one (`name, inputs[0], nested.source`).
- `--schema` printed `Infinity` / `NaN` for a non-finite default (`float("inf")`),
  which is not JSON and which a strict parser rejects. A non-finite value is now
  left out of the schema (the model still applies it). A JSON template writes it
  as the string `"inf"` / `"-inf"` / `"nan"`, which loads back as that float.
- `--generate-config` into a directory that does not exist was a raw traceback
  (exit 1). It is now a usage error (exit 2) naming the path.
- Config files are read and written as UTF-8 on every platform, rather than in
  the locale's encoding (mojibake on a Windows cp1252 locale). A leading
  byte-order mark is tolerated.
- With `config_file=True`, a field whose flag is one typantic injects
  (`--config`, `--generate-config`, `--schema`) was silently unsettable, and
  `--help` listed the flag twice. A field named `config` is the common case. This
  is now a decoration-time error asking for a `cli_name`.
- The flag-collision check now knows a boolean's implicit `--no-x` off switch.
  Fields `cache: bool = True` and `no_cache: bool = False` made `cache`
  impossible to turn off, with no error. A `cli_name` that already carries its
  off switch (`"--color/--no-color"`) no longer collides with itself.
- Positional arguments no longer swap when `config_file` is switched on. Config
  mode kept declaration order while default mode put required arguments first,
  so `cmd A B` filled `src`/`dst` differently in the two modes. Required
  arguments now come first in both, and config mode's `--help` also lists
  required options first.
- `make_main` no longer enters `run_context` for `--help`, `--schema` or
  `--generate-config`. A context that logs to stdout (a Rich console handler,
  say) corrupted `--schema`'s JSON, which the dashboard parses. The
  `--generate-config=x.yaml` spelling is now recognised as a meta flag too, so it
  is no longer timed.
- `make_main` answered a single-command app's `--version 2.1` with the package
  version instead of running the command with its own `version` field. Only a
  lone `--version` / `-V` / `version` is a version request now. Ctrl-C while
  `load_app()` imports exits 130 instead of escaping as a raw `KeyboardInterrupt`.
- Shell completion was missed for a program with a `.` in its name
  (`my.tool`): `make_main` looked for click's variable name, but Typer reads its
  own (`_MY.TOOL_COMPLETE`), so the completion request ran the program.
- A timezone-aware `datetime` could not be entered, because Typer's own
  datetime parser takes only naive formats. Datetimes are now parsed by
  Pydantic, so `2026-09-23T10:00:00+02:00` works. The `--help` metavar is now
  `<datetime>` instead of Typer's format list.
- A `strict=True` model with a `set` or `tuple[X, ...]` field could not run: the
  CLI gathers repeated values into a list, which strict mode rejects. The list is
  now rebuilt into the declared collection first.
- `import typantic` raised `PackageNotFoundError` where the package has no
  installed metadata (a PyInstaller-frozen app, a bare source tree).
  `typantic.__version__` is `"0+unknown"` there, and `typantic --version` and
  the dashboard read the version from it.
- **On Python 3.12 and 3.13 the path picker and the image gallery answered HTTP
  500** for a path under a directory the server cannot enter (a colleague's
  0700 home) or with a component longer than 255 bytes. `Path.is_file()` raises
  there, where 3.14 returns `False`, so the local 3.14 gate never saw it. Every
  path check in the dashboard now answers instead of raising, on every version.
- One bad `output_folder` in a job's config (`~results/x` meant as `~/results/x`,
  or a NUL byte) broke that job's whole gallery, permanently. It is now skipped
  and the job folder's images still show. An undecodable config file is treated
  the same way.
- One file name that is not valid UTF-8 (legal on Linux filesystems) made a
  whole picker listing, or a job's gallery, fail with HTTP 500. Such names are
  now skipped. Creating a folder rejects control characters and undecodable
  names, which would otherwise make a folder no listing could show.
- Opening a command's form in the dashboard answered HTTP 500 instead of
  explaining itself in two cases: the app printed a single byte that is not
  UTF-8 to stderr (a native library's warning, even with a valid schema on
  stdout), or its executable could not be run (a stale shebang after a venv
  moved). The `--schema` child also inherited the server's stdin, so an app
  that prompted hung until the 120 s timeout. Commands now run with no stdin
  and their output is decoded with replacement. A failure to run is reported,
  and a failing app's stderr is trimmed to its tail. A timeout kills the app's
  whole process group, not just the first process.
- `POST /api/commands/refresh` after upgrading an app could be undone by a
  schema fetch already in flight, which stored the old form and served it until
  the next refresh. Several tabs opening the same uncached command also each
  spawned the app. Concurrent first requests now share one fetch, and a fetch
  that straddles a refresh does not store its stale result.
- A scheduler tool (`sacct`, `qstat`) printing a byte that is not UTF-8 (a
  Latin-1 job name, say) made the whole jobs list answer HTTP 500. Scheduler
  tools now run the same way as `--schema`: no stdin, output decoded with
  replacement, and the process group killed on timeout.
- A form field literally named `prefixItems` was renamed to `items`, because the
  schema rewrite ran on property names and data keywords too. It now touches
  only positions that hold schemas.
- 16-bit grayscale images (detector frames, 12-bit captures) got almost
  pure-white gallery thumbnails. Their samples were clipped to 8 bits rather
  than scaled the way a browser displays the full-size image.
- Gallery thumbnails were cached by path, mtime and width only. A renderer fix
  (like the one above) kept serving old tiles, and an image rewritten in place
  within one mtime tick kept its stale thumbnail. The cache key now includes a
  renderer version and the file size. Image URLs also carry the file's mtime, so
  a rewritten image (a `loss.png` updated while the job runs) no longer shows the
  browser's cached copy.
- The gallery listed an image twice when the command's `output_folder` contained
  the job folder, halving the effective limit. It listed all of the job
  folder's images before any from `output_folder`, so a busy job folder could
  hide newer outputs entirely. Images are now gathered from every folder, listed
  once each, and cut to the limit newest first.
- Rendering one thumbnail of a large image made up to five full-size copies
  of it (rotated, composited, converted) before shrinking it. It
  now shrinks straight after decoding. An image larger than Pillow's
  decompression-bomb limit (89.5 megapixels) is no longer decoded at all:
  Pillow only warns below twice that limit, and decoded it in full. A large
  JPEG, which decodes at a fraction of its size, still gets its thumbnail.
- A thumbnail cache that could not be written (a full disk, a read-only home)
  sent every tile the full-size original. The rendered thumbnail is now served
  from memory. A Pillow built without WebP support no longer answers HTTP 500.
- The gallery read each folder's whole listing before its entry cap applied,
  and walked depth first, so one deep subtree could use up the budget before a
  sibling folder was looked at. It now reads lazily and breadth first. The path
  picker likewise stops reading a folder after 200 000 entries and says the
  listing is cut, instead of reading millions of names to show the first page.
- **A relative or `~` jobs root broke every launch** (`--jobs-dir ./jobs`,
  `TYPANTIC_WEB_JOBS_DIR=~/jobs`). A job runs with its own folder as the working
  directory, so its relative `--config` path resolved a second time from inside
  it and the job failed at once, unable to find its config; an unexpanded `~`
  even created a folder literally named `~`. The root is now made absolute when
  the store opens, keeping a symlinked path as spelled.
- **A status check that took a while could undo what happened meanwhile.**
  Refreshing a job stored the copy it had read before asking the backend, and a
  `sacct` query can take seconds. A job restarted in that window was overwritten
  with the old run's outcome, which left the new run running untracked; a
  cancelled job flipped to FAILED; a deleted job came back. A status is now
  stored only while the stored job is still the run that was asked about, and
  every change to a job (refresh, cancel, restart, delete) is made under that
  job's lock.
  - A cached status names its run, so an answer about the run before a restart
    is never reused for the new one.
  - Cancel asks afresh instead of trusting a two-second-old RUNNING for a job
    that has finished since, which recorded CANCELLED over its real outcome.
  - The status cache is synchronised; concurrent requests could raise
    `KeyError` from it.
- A local job that finished in the instant between the two halves of a status
  check was recorded FAILED forever: its exit marker was read (not there yet),
  then its process looked for (gone by then). The marker is now read again
  before a job is called failed.
- A local job whose liveness probe was refused (`EPERM`) was taken to be still
  running, for good. The tracked pid is always typantic's own shell, so a
  refusal means the pid belongs to another user by now and the job is gone.
- A local job's exit marker is now `.typantic-exit` in its folder. The old name,
  `exit_code`, is one an app could plausibly write into its working directory
  itself. A job launched by an older typantic and still running across the
  upgrade writes the old name, which is still read.
- `AliasChoices` fields are settable from the CLI, through their first string
  choice, instead of being rejected at decoration. `config_file="only"` commands
  no longer crash on `AliasChoices` / `AliasPath` fields: templates write the
  alias (an `AliasPath` as its nested keys) and files may use any accepted
  spelling. Only an `AliasPath` through a list index still gets a clear
  decoration-time error.

### Security

- **Breaking: the job store is private.** Each job folder holds the submitted
  config, which can carry a secret the form took as plain text, and the job's
  log. Under the usual 022 umask both were readable by every user of a shared
  login node. The store root and every job folder are now created 0700, and the
  files typantic writes there (config, launch request, log, submit script) 0600
  -- an older file is reset when it is rewritten. An existing store is left as
  it is, and the server warns at start with the `chmod 700` that fixes it.
  **Migration:** if colleagues read results straight out of your job folders,
  point the command's output folder somewhere shared instead.
- **Cancelling or deleting a local job could SIGTERM an unrelated process
  group.** Deleting a job whose stored status still said RUNNING -- its process
  long gone, say across a server restart -- signalled whatever process group its
  pid named by now. A pid is now signalled only while it still runs with the
  start time recorded at launch (where `/proc` tells), and still leads its own
  process group, as the job's shell does. Deleting a job (or a project) asks for
  its live status before it cancels anything.

## [0.7.1] - 2026-09-18

### Security

- `typantic web serve --no-token` is now **refused on a non-loopback host**,
  exiting 2 with an explanation. The flag has always been documented "localhost
  dev only", but nothing enforced it, so `--no-token --host 0.0.0.0` published an
  unauthenticated dashboard to the network. What that exposes is not just the
  path picker but the job launcher itself: anyone who could reach the port could
  run commands as the serving user. Bind a loopback address, or drop `--no-token`
  and use the token that is generated for you.
- The startup banner now states plainly that the dashboard is unauthenticated
  when it runs without a token, instead of silently omitting the token note.

### Changed

- Both GitHub Actions workflows declare `permissions: contents: read` at the
  workflow root, so every job runs with least privilege and any job added later
  inherits it rather than the repository default. The PyPI publish job keeps its
  own `id-token: write`.

## [0.7.0] - 2026-09-17

### Added

- `make_main(load_app, *, package_name, run_context=None)` builds the `main()` a
  package exposes as its console script. It answers `--version` from package
  metadata *before* calling `load_app()`, so an app that imports a heavy stack
  still responds instantly; hands shell-completion requests straight to Typer;
  logs the wall-clock duration of a real run (but not of `--help`, `--schema` or
  `--generate-config`, whose stdout stays machine-readable); and turns an
  uncaught exception into exit 1 with the traceback logged. `run_context` wraps
  the run in a caller-supplied context manager — logging setup that must be torn
  down even when the command raises — entered after the version and completion
  short-circuits so neither pays for it.

### Changed

- **Dependency floors raised to the versions actually resolved.** For
  `typantic[web]` this is a real minimum bump: `fastapi>=0.141.1` (was 0.138),
  `uvicorn>=0.53.0` (was 0.49), `websockets>=17.1` (was 16) and `pillow>=12.3.0`
  (was 11). Base typantic moves to `pydantic>=2.13.5` (was 2.10), `pyyaml>=6.0.3`
  and `typer>=0.27.2` (was 0.27). The supported Python range is **unchanged** —
  still 3.12 through 3.15.
- The README now documents `load_config_file`, `build_config_template` and
  `write_config_template`. All three have been exported since 0.2.0, but the
  Config files section only ever covered the `--config` / `--generate-config`
  flags, so three of the five public names had no documentation.

### Fixed

- The web form no longer rejects leaving an optional `X | None` scalar field
  blank. Collapsing a nullable union kept the field's `default: null` while
  narrowing its `type` to the non-null branch, so the emitted schema was
  self-contradictory (`type: number` with `default: null`) and RJSF's validator
  refused to submit until a value was entered. Nullable scalars are now emitted
  as `type: [<type>, "null"]` (rendered as the same single input) so the null
  default and an empty input validate; for non-scalar nullable branches
  (`$ref`/enum, array, object) the invalid null default is dropped so the model's
  own `None` default applies.

## [0.6.0] - 2026-07-21

### Added

- `typantic web serve --log-level` sets the uvicorn log level (critical, error,
  warning, info, debug, or trace); previously it was fixed at `info`.
- `POST /api/commands/refresh` re-discovers installed apps at runtime, so a
  newly `pip install`-ed command appears without restarting the server.
- The dashboard's `web/src/types.ts` is now generated from the Pydantic models
  by `scripts/gen_types.py` (`make gen-types`), and a CI check fails the build if
  it drifts. It previously mirrored the models by hand and had silently gone out
  of sync.

### Changed

- The web API responses that were untyped `dict`s are now Pydantic models
  (`/api/meta`, the backends list, the `/api/fs` directory listing, and job
  images), so their shape is validated and shows up in the OpenAPI schema. The
  emitted JSON is unchanged.
- The web dashboard now mirrors the `--title` brand into the browser tab title;
  previously the tab always read "typantic web" regardless of `--title`.
- `typantic web serve` now prints a copy-paste-ready `ssh -N -L …` tunnel line
  with the serving host's user and name filled in, instead of a `<this-host>`
  placeholder.

### Fixed

- A finished job's detail page no longer polls the server forever: the 2-second
  status poll and the 3-second output-image poll now stop once the job reaches a
  terminal state.
- The local/process backend no longer leaves a zombie when `poll` reads the
  exit-code marker in the narrow window before the wrapper shell has exited — the
  reap now retries briefly until the child is collected.
- A crashed local job no longer reads as `running` after a server restart when
  its pid has been recycled onto an unrelated process: the process start-time
  recorded at launch is compared on each poll (Linux; a no-op where `/proc` is
  absent, falling back to the previous liveness probe).
- The launcher's poll cache is now bounded (fixed size, least-recently-updated
  eviction), so a job that terminated without a later refresh can no longer leave
  its entry behind indefinitely.

### Dependencies

- Dashboard frontend majors: upgraded React JSON Schema Form (`@rjsf/core`,
  `@rjsf/utils`, `@rjsf/validator-ajv8`) from 5 to 6, TypeScript from 6 to 7,
  and `lucide-react` to 1.25. RJSF v6 renames the custom-template `idSchema`
  prop to `fieldPathId` and prefixes its generated marker classes
  (`form-group` → `rjsf-field`, `array-item` → `rjsf-array-item`); the affected
  template and the dashboard CSS that targeted those classes were migrated. The
  Python API is unchanged.
- Updated the locked `websockets` (part of the `[web]` extra's stack) from 16.1
  to 16.1.1. The declared floor (`websockets>=16`) is unchanged.
- CI only, no effect on the published package: bumped `actions/checkout` from
  7.0.0 to 7.0.1 and `pypa/gh-action-pypi-publish` from 1.14.0 to 1.14.1.

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