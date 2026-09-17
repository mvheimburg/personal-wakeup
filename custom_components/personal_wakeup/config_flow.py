"""Config flow for the Personal Wakeup integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    CONF_LIGHT_ENTITY,
    CONF_MA_PLAYER_ENTITY,
    CONF_PERSON_ENTITIES,
    CONF_PLAYLIST_OPTIONS,
    CONF_REQUIRE_HOME,
    CONF_WAKE_MODE,
    DOMAIN,
    WAKE_MODES,
)
from .utils import normalize_playlists, selected_people, validate_wiring

DEFAULT_NAME = "Wakeup Alarm"
DEFAULT_REQUIRE_HOME = False


def _base_schema(
    defaults: dict[str, Any] | None = None, *, include_require_home: bool
) -> vol.Schema:
    defaults = defaults or {}
    playlists_default_str = ",".join(normalize_playlists(defaults.get(CONF_PLAYLIST_OPTIONS, [])))

    fields: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
        vol.Optional(
            CONF_LIGHT_ENTITY, description={"suggested_value": defaults.get(CONF_LIGHT_ENTITY)}
        ): selector({"entity": {"domain": "light"}}),
        vol.Optional(
            CONF_MA_PLAYER_ENTITY,
            description={"suggested_value": defaults.get(CONF_MA_PLAYER_ENTITY)},
        ): selector({"entity": {"domain": "media_player"}}),
        vol.Optional(CONF_PERSON_ENTITIES, default=selected_people(defaults)): selector(
            {"entity": {"domain": "person", "multiple": True}}
        ),
    }
    fields[vol.Required(CONF_WAKE_MODE, default=defaults.get(CONF_WAKE_MODE, "both"))] = selector(
        {"select": {"options": list(WAKE_MODES)}}
    )
    if include_require_home:
        # Only the initial value: afterwards require_home is a runtime setting
        # controlled from the card / set_config service.
        fields[
            vol.Required(
                CONF_REQUIRE_HOME,
                default=defaults.get(CONF_REQUIRE_HOME, DEFAULT_REQUIRE_HOME),
            )
        ] = selector({"boolean": {}})
    fields[vol.Optional(CONF_PLAYLIST_OPTIONS, default=playlists_default_str)] = str
    return vol.Schema(fields)


def _validate(user_input: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Return (errors, options) for a submitted form."""
    errors: dict[str, str] = {}
    try:
        validate_wiring(user_input)
    except vol.Invalid as exc:
        errors["base"] = str(exc)

    options = {k: v for k, v in user_input.items() if k not in (CONF_NAME, CONF_PLAYLIST_OPTIONS)}
    options[CONF_PERSON_ENTITIES] = selected_people(user_input)
    options[CONF_WAKE_MODE] = user_input.get(CONF_WAKE_MODE, "both")
    options[CONF_PLAYLIST_OPTIONS] = normalize_playlists(user_input.get(CONF_PLAYLIST_OPTIONS))
    return errors, options


class PersonalWakeupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Personal Wakeup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, options = _validate(user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={}, options=options
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_base_schema(
                user_input or {CONF_NAME: DEFAULT_NAME}, include_require_home=True
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PersonalWakeupOptionsFlow:
        return PersonalWakeupOptionsFlow(config_entry)


class PersonalWakeupOptionsFlow(OptionsFlow):
    """Handle options for Personal Wakeup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, options = _validate(user_input)
            if not errors:
                # Preserve the initial require_home so a fresh entity without
                # restore state still gets a sensible default.
                if CONF_REQUIRE_HOME in self._entry.options:
                    options[CONF_REQUIRE_HOME] = self._entry.options[CONF_REQUIRE_HOME]
                # Commit title and options together so only one reload runs.
                self.hass.config_entries.async_update_entry(
                    self._entry, title=user_input[CONF_NAME], options=options
                )
                return self.async_create_entry(title="", data=options)

        current = (
            user_input
            if user_input is not None
            else {**self._entry.options, CONF_NAME: self._entry.title}
        )
        return self.async_show_form(
            step_id="init",
            data_schema=_base_schema(current, include_require_home=False),
            errors=errors,
        )
