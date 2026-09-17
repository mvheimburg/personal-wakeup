"""Validation and normalization shared by flows and services."""

import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.const import WEEKDAYS

from .const import (
    CONF_LIGHT_ENTITY,
    CONF_MA_PLAYER_ENTITY,
    CONF_PERSON_ENTITIES,
    CONF_PERSON_ENTITY,
    CONF_WAKE_MODE,
    WAKE_MODES,
)


def normalize_playlists(raw: str | list[str] | None) -> list[str]:
    """Turn a comma-separated string or list into a clean list of playlist IDs."""
    if isinstance(raw, list):
        return [p for p in raw if p]  # already a list
    if not raw:
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def selected_people(options: Mapping[str, Any]) -> list[str]:
    """Read ordered people, respecting an explicit empty new selection."""
    raw = (
        options.get(CONF_PERSON_ENTITIES)
        if CONF_PERSON_ENTITIES in options
        else [options.get(CONF_PERSON_ENTITY)]
    )
    return list(dict.fromkeys(person for person in (raw or []) if person))


def validate_wiring(options: Mapping[str, Any]) -> None:
    """Validate complete wiring before options or runtime settings change."""
    mode = options.get(CONF_WAKE_MODE, "both")
    if mode not in WAKE_MODES:
        raise vol.Invalid("invalid_wake_mode")
    if mode in ("lights", "both") and not options.get(CONF_LIGHT_ENTITY):
        raise vol.Invalid("no_light_entity")
    if mode in ("music", "both") and not options.get(CONF_MA_PLAYER_ENTITY):
        raise vol.Invalid("no_player_entity")


def validate_day_times(value: Any) -> dict[str, str]:
    """Return JSON-safe HH:MM overrides; reject the whole invalid mapping."""
    import voluptuous as vol

    if not isinstance(value, dict):
        raise vol.Invalid("day_times must be a mapping")
    for day, clock in value.items():
        if (
            day not in WEEKDAYS
            or not isinstance(clock, str)
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock)
        ):
            raise vol.Invalid("day_times requires weekdays and HH:MM times")
    return dict(value)
