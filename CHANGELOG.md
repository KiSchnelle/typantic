# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `add_command` is now generic over the model type, so the `handler` callback is
  typed against the concrete model class instead of the base `BaseModel`. Type
  checkers now infer the precise model type passed to the handler.

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