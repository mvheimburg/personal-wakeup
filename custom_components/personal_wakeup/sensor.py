"""Sensor platform: one status entity per config entry plus entity services."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm import WakeupAlarmEntity
from .const import (
    ATTR_AUTO_OFF_MINUTES,
    ATTR_DURATION_MINUTES,
    ATTR_ENABLED,
    ATTR_FADE_DURATION,
    ATTR_FADE_MUSIC_DURATION,
    ATTR_PLAYLIST,
    ATTR_REQUIRE_HOME,
    ATTR_SKIP_NEXT,
    ATTR_SNOOZE_MINUTES,
    ATTR_TIME_OF_DAY,
    ATTR_VOLUME,
    ATTR_WEEKDAYS,
    SERVICE_SET_CONFIG,
    SERVICE_SNOOZE,
    SERVICE_STOP,
    SERVICE_TRIGGER_NOW,
)

_LOGGER = logging.getLogger(__name__)

SET_CONFIG_FIELDS = {
    vol.Optional(ATTR_ENABLED): cv.boolean,
    vol.Optional(ATTR_TIME_OF_DAY): vol.Any(cv.time, cv.string),
    vol.Optional(ATTR_WEEKDAYS): cv.weekdays,
    vol.Optional(ATTR_SKIP_NEXT): cv.boolean,
    vol.Optional(ATTR_FADE_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional(ATTR_FADE_MUSIC_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional(ATTR_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    vol.Optional(ATTR_PLAYLIST): cv.string,
    vol.Optional(ATTR_REQUIRE_HOME): cv.boolean,
    vol.Optional(ATTR_SNOOZE_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
    vol.Optional(ATTR_AUTO_OFF_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
}

SNOOZE_FIELDS = {
    vol.Optional(ATTR_DURATION_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Personal Wakeup alarm entity from a config entry."""
    async_add_entities([WakeupAlarmEntity(hass, entry)])

    # Entity services: HA resolves entity_id / device / area targets for us and
    # registers each service only once per domain.
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_CONFIG, SET_CONFIG_FIELDS, "async_set_config"
    )
    platform.async_register_entity_service(SERVICE_TRIGGER_NOW, None, "async_trigger")
    platform.async_register_entity_service(SERVICE_SNOOZE, SNOOZE_FIELDS, "async_snooze")
    platform.async_register_entity_service(SERVICE_STOP, None, "async_stop")
