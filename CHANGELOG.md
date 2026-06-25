# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `config_file="only"` on `pydantic_to_typer` / `add_command` registers a
  **file-only** command — only `--config` / `--generate-config`, no per-field
  flags — for settings models that cannot map onto flat flags (nested-model
  lists, `scalar | (min, max)` ranges).
- `add_command(..., help=...)` to set the command's help text explicitly
  (otherwise the handler's docstring is used).

### Fixed

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