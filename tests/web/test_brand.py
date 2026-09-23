import logging

import pytest
from pydantic import ValidationError

from typantic.web import brand as brand_mod
from typantic.web.brand import discover_brand, resolve_brand
from typantic.web.models import Brand

ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><circle r="4"/></svg>'


# --- the Brand model ---


def test_the_default_brand_is_typantics():
    brand = Brand()
    assert (brand.title, brand.lead, brand.rest) == ("typantic web", "typantic", "web")
    assert brand.icon is None
    assert brand.accent is None


@pytest.mark.parametrize(
    ("title", "lead", "rest"),
    [("catchEM", "catchEM", ""), ("my lab tools", "my", "lab tools")],
)
def test_the_wordmark_splits_the_title_at_its_first_space(title, lead, rest):
    brand = Brand(title=title)
    assert (brand.lead, brand.rest) == (lead, rest)


def test_an_explicit_wordmark_is_kept():
    brand = Brand(title="catchEM", lead="catch", rest="EM")
    assert (brand.lead, brand.rest) == ("catch", "EM")


@pytest.mark.parametrize(
    "icon",
    [
        ICON,
        f'<?xml version="1.0" encoding="UTF-8"?>\n{ICON}',
        f'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN">{ICON}',
        f"  <!-- exported by a tool -->\n{ICON}",
    ],
)
def test_an_svg_document_is_an_icon(icon):
    assert Brand(icon=icon).icon == icon


@pytest.mark.parametrize(
    "icon",
    [
        "logo.svg",  # a path, not markup
        "<html><body><svg/></body></html>",
        "<!-- unterminated <svg>",
        f"<svg>{'x' * 70_000}</svg>",  # over 64 KiB
    ],
)
def test_anything_else_is_not(icon):
    with pytest.raises(ValidationError):
        Brand(icon=icon)


@pytest.mark.parametrize("accent", ["#5AA9FF", "#22d3ee"])
def test_an_accent_is_a_hex_colour(accent):
    assert Brand(accent=accent).accent == accent


@pytest.mark.parametrize("accent", ["5AA9FF", "#fff", "red", "#5AA9FF\n"])
def test_other_colour_spellings_are_refused(accent):
    with pytest.raises(ValidationError):
        Brand(accent=accent)


def test_unknown_brand_keys_are_ignored():
    assert Brand.model_validate({"title": "x", "theme": "dark"}).title == "x"


# --- discovery ---


class Entry:
    def __init__(self, name, value):
        self.name = name
        self._value = value

    def load(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


@pytest.fixture
def installed(monkeypatch):
    entries = []

    def entry_points(*, group):
        assert group == "typantic.web_brand"
        return list(entries)

    monkeypatch.setattr(brand_mod, "entry_points", entry_points)
    return entries


def test_without_an_installed_brand_the_default_applies(installed):
    assert discover_brand() == Brand()


def test_an_installed_mapping_is_the_brand(installed):
    installed.append(Entry("catchem", {"title": "catchEM", "accent": "#5AA9FF"}))
    assert discover_brand() == Brand(title="catchEM", accent="#5AA9FF")


def test_an_installed_brand_instance_is_the_brand(installed):
    installed.append(Entry("lab", Brand(title="Lab")))
    assert discover_brand().title == "Lab"


def test_a_broken_or_invalid_brand_is_skipped(installed, caplog):
    installed.append(Entry("broken", ImportError("no module named lab_brand")))
    installed.append(Entry("invalid", {"accent": "blue"}))
    installed.append(Entry("valid", {"title": "Valid"}))
    assert discover_brand().title == "Valid"
    assert "'broken'" in caplog.text
    assert "'invalid'" in caplog.text


def test_the_first_brand_by_name_wins_and_the_rest_are_reported(installed, caplog):
    installed.append(Entry("zeta", {"title": "Zeta"}))
    installed.append(Entry("alpha", {"title": "Alpha"}))
    with caplog.at_level(logging.WARNING, logger="typantic.web"):
        assert discover_brand().title == "Alpha"
    assert "'zeta'" in caplog.text


# --- overrides ---


def test_no_overrides_keep_the_brand_as_it_is():
    brand = Brand(title="catchEM", accent="#5AA9FF")
    assert resolve_brand(brand) is brand
    assert resolve_brand(None) == Brand()


def test_a_title_override_splits_its_own_wordmark():
    brand = Brand(title="catchEM", lead="catch", rest="EM")
    resolved = resolve_brand(brand, title="catchEM dev")
    assert (resolved.title, resolved.lead, resolved.rest) == (
        "catchEM dev",
        "catchEM",
        "dev",
    )


def test_icon_and_accent_overrides_are_checked_like_the_rest():
    resolved = resolve_brand(Brand(title="catchEM"), icon=ICON, accent="#ff0000")
    assert (resolved.title, resolved.icon, resolved.accent) == (
        "catchEM",
        ICON,
        "#ff0000",
    )
    with pytest.raises(ValidationError):
        resolve_brand(Brand(), accent="crimson")
