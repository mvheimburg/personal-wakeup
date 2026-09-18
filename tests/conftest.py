"""Shared fixtures for Personal Wakeup tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.personal_wakeup.const import (
    CONF_LIGHT_ENTITY,
    CONF_MA_PLAYER_ENTITY,
    CONF_PERSON_ENTITY,
    CONF_PLAYLIST_OPTIONS,
    CONF_REQUIRE_HOME,
    DOMAIN,
)

ENTITY_ID = "sensor.matilde_wakeup"
LIGHT = "light.bedroom"
PLAYER = "media_player.bedroom"
PERSON = "person.matilde"
PLAYLISTS = ["library://playlist/1", "library://playlist/2"]

# A fixed "now": Monday 2026-09-14 22:00 UTC (00:00 Tue in Europe/Copenhagen)
START = datetime(2026, 9, 14, 22, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def calls(hass: HomeAssistant):
    """Mock every service the alarm calls and expose the call lists."""
    return {
        "light_on": async_mock_service(hass, "light", "turn_on"),
        "volume_set": async_mock_service(hass, "media_player", "volume_set"),
        "media_stop": async_mock_service(hass, "media_player", "media_stop"),
        "play_media": async_mock_service(hass, "music_assistant", "play_media"),
    }


@pytest.fixture
def events(hass: HomeAssistant):
    captured = []
    hass.bus.async_listen(f"{DOMAIN}_event", lambda e: captured.append(e.data))
    return captured


@pytest.fixture
def entry(hass: HomeAssistant, require_home: bool = False) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Matilde",
        data={},
        options={
            CONF_LIGHT_ENTITY: LIGHT,
            CONF_MA_PLAYER_ENTITY: PLAYER,
            CONF_PERSON_ENTITY: PERSON,
            CONF_REQUIRE_HOME: False,
            CONF_PLAYLIST_OPTIONS: PLAYLISTS,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.states.async_set(PERSON, "home")
    hass.states.async_set(LIGHT, "off")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def set_config(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call(
        DOMAIN, "set_config", {"entity_id": ENTITY_ID, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(DOMAIN, service, {"entity_id": ENTITY_ID, **data}, blocking=True)
    await hass.async_block_till_done()


def state(hass: HomeAssistant):
    return hass.states.get(ENTITY_ID)


def attr(hass: HomeAssistant, name: str):
    return state(hass).attributes.get(name)


async def advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Move frozen time forward and fire every timer that became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def jump_to(hass: HomeAssistant, freezer, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def settle(hass: HomeAssistant) -> None:
    """Let background tasks progress a few loop iterations."""
    for _ in range(10):
        await hass.async_block_till_done()
