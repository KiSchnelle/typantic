"""Discover launchable commands from installed apps, without importing them.

The gateway enumerates the ``typantic.web_commands`` entry-point group and loads
each app's list of command mappings. Those modules are dependency-free by
contract, so discovery never imports an app's runtime code — heavy dependencies
never enter the web process. A newly installed app shows up automatically, with
no central file edited.

An app registers its commands in ``pyproject.toml``::

    [project.entry-points."typantic.web_commands"]
    myapp = "myapp.web_meta:WEB_COMMANDS"

where ``WEB_COMMANDS`` is a ``list[dict]`` of :class:`CommandMeta` fields.
"""

import logging
from importlib.metadata import entry_points

from pydantic import ValidationError

from typantic.web.models import CommandMeta

_ENTRY_POINT_GROUP = "typantic.web_commands"

logger = logging.getLogger("typantic.web")


def discover_commands() -> list[CommandMeta]:
    """Return every launchable command from installed apps, sorted by key.

    Each entry point resolves to a list of plain mappings; malformed mappings
    are skipped with a warning (one bad command never hides the rest). Loading
    an entry point that raises (e.g. a broken app) is likewise isolated per app.

    Returns:
        The discovered commands, ordered by ``app`` then ``command``.
    """
    commands: list[CommandMeta] = []
    for entry in entry_points(group=_ENTRY_POINT_GROUP):
        try:
            raw = entry.load()
        except Exception:  # a broken app must not hide the others
            logger.exception("Failed to load web commands from %r", entry.name)
            continue
        commands.extend(_parse_entry(entry.name, raw))
    commands.sort(key=lambda meta: (meta.app, meta.command))
    return _deduplicate(commands)


def _deduplicate(commands: list[CommandMeta]) -> list[CommandMeta]:
    """Keep the first command per key, warning about any it shadows.

    The key is what everything downstream looks a command up by, so a duplicate
    would show twice in the catalog while every lookup resolved to just one of
    them -- clicking either would launch the same command.
    """
    seen: dict[str, CommandMeta] = {}
    for meta in commands:
        if meta.key in seen:
            logger.warning(
                "Ignoring duplicate command %r (already provided by another entry "
                "point); commands must have a unique app/command key.",
                meta.key,
            )
            continue
        seen[meta.key] = meta
    return list(seen.values())


def _parse_entry(entry_name: str, raw: object) -> list[CommandMeta]:
    """Validate one entry point's payload into :class:`CommandMeta` objects."""
    if not isinstance(raw, list):
        logger.warning(
            "Entry point %r did not resolve to a list of command mappings; skipping",
            entry_name,
        )
        return []
    parsed: list[CommandMeta] = []
    for item in raw:
        try:
            parsed.append(CommandMeta.model_validate(item))
        except ValidationError:
            logger.warning("Skipping malformed command from %r: %r", entry_name, item)
    return parsed

