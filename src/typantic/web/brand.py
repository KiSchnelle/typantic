"""The dashboard's brand: installed by a package, overridden for a run.

A package presents the dashboard as its own by registering a :class:`Brand`, or
a plain mapping of its fields, under the ``typantic.web_brand`` entry-point
group::

    [project.entry-points."typantic.web_brand"]
    myapp = "myapp.web_brand:BRAND"

Only ``typantic web serve`` discovers it. :func:`~typantic.web.make_api` and
:func:`~typantic.web.serve` show the brand they are given, so an app built in a
test, or embedded in another server, never picks one up from whatever happens to
be installed.
"""

import logging
from importlib.metadata import entry_points

from typantic.web.models import Brand

_ENTRY_POINT_GROUP = "typantic.web_brand"

logger = logging.getLogger("typantic.web")


def discover_brand() -> Brand:
    """The installed brand, or typantic's own when there is none.

    Entry points are tried in name order: the first that loads and validates
    wins, and any other valid one is reported as ignored. One that fails to load
    or validate is skipped with a warning, so a broken package never takes the
    dashboard down.
    """
    found: list[tuple[str, Brand]] = []
    for entry in sorted(entry_points(group=_ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            found.append((entry.name, Brand.model_validate(entry.load())))
        except Exception:  # a broken package must not take the dashboard down
            logger.exception("Ignoring the brand from %r", entry.name)
    if not found:
        return Brand()
    (winner, brand), *others = found
    for name, _ in others:
        logger.warning(
            "Ignoring the brand from %r: %r already provides one.",
            name,
            winner,
        )
    return brand


def resolve_brand(
    base: Brand | None = None,
    *,
    title: str | None = None,
    icon: str | None = None,
    accent: str | None = None,
) -> Brand:
    """``base`` (typantic's own brand when ``None``) with the given fields replaced.

    Rebuilt through validation, so an override is checked like any brand. A new
    title also gets its own wordmark: ``base`` holds the split of its old one.

    Raises:
        pydantic.ValidationError: If an override is not a valid brand field.
    """
    brand = base or Brand()
    overrides = {
        name: value
        for name, value in (("title", title), ("icon", icon), ("accent", accent))
        if value is not None
    }
    if not overrides:
        return brand
    if "title" in overrides:
        overrides |= {"lead": "", "rest": ""}
    return Brand.model_validate(brand.model_dump() | overrides)
