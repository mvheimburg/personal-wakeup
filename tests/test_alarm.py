"""State-machine tests for the wakeup alarm entity."""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.personal_wakeup.const import (
    STATE_ARMED,
    STATE_DISARMED,
    STATE_RINGING,
    STATE_RISING,
    STATE_SNOOZED,
)

from .conftest import (
    ENTITY_ID,
    LIGHT,
    PLAYER,
    PLAYLISTS,
    START,
    advance,
    attr,
    call,
    jump_to,
    set_config,
    settle,
    setup_entry,
    state,
)

pytestmark = pytest.mark.usefixtures("calls", "events")

# Short fades so a run completes in a handful of 5 s ticks:
# light 20 s, music 10 s centred on the light end -> music starts at 15 s.
FAST = {"fade_duration": 20, "fade_music_duration": 10, "auto_off_minutes": 30}


async def arm_fast(hass, entry, **extra):
    await setup_entry(hass, entry)
    await set_config(hass, time_of_day="07:00", **FAST, **extra)


async def fire_and_ring(hass, freezer):
    """Jump to the alarm time and tick through the fade until ringing."""
    next_fire = dt_util.parse_datetime(attr(hass, "next_fire"))
    await jump_to(hass, freezer, next_fire)
    await settle(hass)
    assert state(hass).state == STATE_RISING
    for _ in range(8):
        await advance(hass, freezer, 5)
        await settle(hass)
        if state(hass).state == STATE_RINGING:
            break
    assert state(hass).state == STATE_RINGING


@pytest.fixture(autouse=True)
def _freeze(freezer):
    freezer.move_to(START)


async def test_setup_arms_for_next_time(hass: HomeAssistant, entry) -> None:
    await setup_entry(hass, entry)
    s = state(hass)
    assert s is not None
    assert s.state == STATE_ARMED
    assert s.attributes["playlist"] == PLAYLISTS[0]
    assert s.attributes["playlist_options"] == PLAYLISTS
    assert s.attributes["enabled"] is True
    assert s.attributes["can_stop"] is False
    next_fire = dt_util.parse_datetime(s.attributes["next_fire"])
    assert next_fire > dt_util.utcnow()
    assert dt_util.as_local(next_fire).strftime("%H:%M") == "07:00"


@pytest.mark.parametrize("person", ["person.matilde", "person.someone_else", ""])
async def test_new_sensor_uses_entry_name_regardless_of_person(hass, entry, person):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "person_entity": person}
    )
    await setup_entry(hass, entry)

    sensor = hass.states.get("sensor.matilde_alarm")
    assert sensor is not None
    assert sensor.attributes["friendly_name"] == "Matilde alarm"
    assert sensor.attributes["person_entity"] == (person or None)


async def test_existing_sensor_keeps_id_and_uses_entry_name(hass, entry):
    registry = er.async_get(hass)
    registered = registry.async_get_or_create(
        "sensor",
        "personal_wakeup",
        entry.entry_id,
        suggested_object_id="matilde_wakeup",
        config_entry=entry,
    )
    await setup_entry(hass, entry)

    sensor = hass.states.get(registered.entity_id)
    assert sensor is not None
    assert sensor.entity_id == "sensor.matilde_wakeup"
    assert sensor.attributes["friendly_name"] == "Matilde alarm"
    assert hass.states.get("sensor.matilde_alarm") is None


async def test_entry_rename_updates_display_name_without_changing_sensor_id(hass, entry):
    await setup_entry(hass, entry)
    hass.config_entries.async_update_entry(entry, title="Weekend wakeup")
    await hass.async_block_till_done()

    sensor = hass.states.get("sensor.matilde_alarm")
    assert sensor is not None
    assert sensor.attributes["friendly_name"] == "Weekend wakeup"
    assert hass.states.get("sensor.weekend_wakeup") is None


async def test_device_settings_persist_and_drive_next_run(hass, entry, calls):
    await setup_entry(hass, entry)
    await set_config(hass, light_entity="light.new", ma_player_entity="media_player.new")
    assert entry.options["light_entity"] == "light.new"
    assert entry.options["ma_player_entity"] == "media_player.new"
    assert entry.options["playlist_options"] == PLAYLISTS
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert attr(hass, "light_entity") == "light.new"
    assert attr(hass, "player_entity") == "media_player.new"
    await call(hass, "trigger_now")
    await settle(hass)
    assert calls["light_on"][-1].data["entity_id"] == "light.new"
    await call(hass, "stop")
    assert calls["media_stop"][-1].data["entity_id"] == "media_player.new"


async def test_device_change_stops_original_player(hass, entry, calls):
    await setup_entry(hass, entry)
    await call(hass, "trigger_now")
    await settle(hass)
    await set_config(hass, ma_player_entity="media_player.new")
    assert calls["media_stop"][-1].data["entity_id"] == PLAYER
    assert state(hass).state == STATE_ARMED


async def test_person_selection_can_be_changed_and_cleared(hass, entry):
    await setup_entry(hass, entry)
    await set_config(hass, person_entity="person.other")
    assert attr(hass, "person_entity") == "person.other"
    await set_config(hass, person_entity="")
    assert attr(hass, "person_entity") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("light_entity", PLAYER),
        ("ma_player_entity", LIGHT),
        ("person_entity", LIGHT),
        ("light_entity", ""),
        ("ma_player_entity", ""),
    ],
)
async def test_device_domains_are_validated(hass, entry, field, value):
    await setup_entry(hass, entry)
    with pytest.raises(vol.Invalid):
        await set_config(hass, **{field: value})


async def test_full_run_then_stop(hass, entry, freezer, calls, events) -> None:
    await arm_fast(hass, entry)
    await fire_and_ring(hass, freezer)

    # Light ramped to full, music started and ramped, alarm keeps ringing.
    brightnesses = [c.data["brightness"] for c in calls["light_on"]]
    assert brightnesses[-1] == 255
    assert brightnesses == sorted(brightnesses)
    assert all(c.data["transition"] == 5 for c in calls["light_on"])
    assert len(calls["play_media"]) == 1
    assert calls["play_media"][0].data["media_id"] == PLAYLISTS[0]
    assert calls["volume_set"][-1].data["volume_level"] == pytest.approx(0.25)
    assert attr(hass, "can_stop") is True
    assert attr(hass, "can_snooze") is True
    assert [e["type"] for e in events] == ["triggered", "ringing"]

    # Still ringing ten minutes later: no premature re-arm.
    await advance(hass, freezer, 600)
    await settle(hass)
    assert state(hass).state == STATE_RINGING

    await call(hass, "stop")
    assert state(hass).state == STATE_ARMED
    assert len(calls["media_stop"]) == 1
    assert events[-1]["type"] == "stopped"
    assert attr(hass, "can_stop") is False
    next_fire = dt_util.parse_datetime(attr(hass, "next_fire"))
    assert next_fire - dt_util.utcnow() > timedelta(hours=23)


async def test_snooze_resumes_without_replaying_sunrise(
    hass, entry, freezer, calls, events
) -> None:
    await arm_fast(hass, entry)
    await fire_and_ring(hass, freezer)
    light_calls_before = len(calls["light_on"])
    hass.states.async_set(LIGHT, "on", {"brightness": 255})

    await call(hass, "snooze", duration_minutes=5)
    assert state(hass).state == STATE_SNOOZED
    assert len(calls["media_stop"]) == 1
    assert attr(hass, "can_stop") is True
    assert attr(hass, "can_snooze") is True
    snooze_until = dt_util.parse_datetime(attr(hass, "snooze_until"))
    assert snooze_until == dt_util.utcnow() + timedelta(minutes=5)
    assert attr(hass, "next_fire") == attr(hass, "snooze_until")
    assert events[-1]["type"] == "snoozed"
    assert events[-1]["minutes"] == 5

    # Changing a setting while snoozed must not cancel the snooze.
    await set_config(hass, volume=0.5)
    assert state(hass).state == STATE_SNOOZED
    assert attr(hass, "snooze_until") == snooze_until.isoformat()

    # Snooze fires: music comes back right away, light is not faded again.
    await jump_to(hass, freezer, snooze_until)
    await settle(hass)
    assert state(hass).state == STATE_RISING
    assert len(calls["play_media"]) == 2
    assert len(calls["light_on"]) == light_calls_before  # already at 255
    for _ in range(8):
        await advance(hass, freezer, 5)
        await settle(hass)
        if state(hass).state == STATE_RINGING:
            break
    assert state(hass).state == STATE_RINGING
    assert calls["volume_set"][-1].data["volume_level"] == pytest.approx(0.5)

    await call(hass, "stop")
    assert state(hass).state == STATE_ARMED
    assert len(calls["media_stop"]) == 2


async def test_stop_while_snoozed_rearms(hass, entry, freezer, calls) -> None:
    await arm_fast(hass, entry)
    await fire_and_ring(hass, freezer)
    await call(hass, "snooze")
    assert state(hass).state == STATE_SNOOZED
    assert attr(hass, "snooze_minutes") == 10

    await call(hass, "stop")
    assert state(hass).state == STATE_ARMED
    assert attr(hass, "snooze_until") is None
    next_fire = dt_util.parse_datetime(attr(hass, "next_fire"))
    assert dt_util.as_local(next_fire).strftime("%H:%M") == "07:00"

    # The cancelled snooze never fires.
    await advance(hass, freezer, 15 * 60)
    await settle(hass)
    assert state(hass).state == STATE_ARMED
    assert len(calls["play_media"]) == 1


async def test_disable_during_run_stops_everything(hass, entry, freezer, calls, events) -> None:
    await arm_fast(hass, entry)
    await fire_and_ring(hass, freezer)

    await set_config(hass, enabled=False)
    assert state(hass).state == STATE_DISARMED
    assert len(calls["media_stop"]) == 1
    assert events[-1]["type"] == "stopped"
    assert events[-1]["reason"] == "disabled"
    assert attr(hass, "next_fire") is None

    await set_config(hass, enabled=True)
    assert state(hass).state == STATE_ARMED
    assert attr(hass, "next_fire") is not None


async def test_auto_off(hass, entry, freezer, calls, events) -> None:
    await arm_fast(hass, entry)
    await fire_and_ring(hass, freezer)

    await advance(hass, freezer, 31 * 60)
    await settle(hass)
    assert state(hass).state == STATE_ARMED
    assert len(calls["media_stop"]) == 1
    assert events[-1]["type"] == "auto_off"


async def test_skip_next_skips_one_occurrence(hass, entry, freezer, events) -> None:
    await arm_fast(hass, entry)
    first = dt_util.parse_datetime(attr(hass, "next_fire"))

    await set_config(hass, skip_next=True)
    assert attr(hass, "skipped_fire") == first.isoformat()
    second = dt_util.parse_datetime(attr(hass, "next_fire"))
    assert second == first + timedelta(days=1)

    await jump_to(hass, freezer, first)
    await settle(hass)
    assert state(hass).state == STATE_ARMED
    assert attr(hass, "skip_next") is False
    assert attr(hass, "skipped_fire") is None
    assert dt_util.parse_datetime(attr(hass, "next_fire")) == second
    assert events[-1]["type"] == "skipped"
    assert events[-1]["reason"] == "skip_next"

    await set_config(hass, skip_next=False)
    assert attr(hass, "skipped_fire") is None


async def test_weekdays(hass, entry) -> None:
    await arm_fast(hass, entry)
    # START is Tuesday 00:00 local; only Thursday selected.
    await set_config(hass, weekdays=["thu"])
    next_fire = dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire")))
    assert next_fire.strftime("%a %H:%M") == "Thu 07:00"
    assert attr(hass, "weekdays") == ["thu"]

    # Empty selection means every day.
    await set_config(hass, weekdays=[])
    assert attr(hass, "weekdays") == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    next_fire = dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire")))
    assert next_fire.strftime("%a %H:%M") == "Tue 07:00"


async def test_require_home_skips_when_away(hass, entry, freezer, calls, events) -> None:
    await arm_fast(hass, entry, require_home=True)
    hass.states.async_set("person.matilde", "not_home")
    first = dt_util.parse_datetime(attr(hass, "next_fire"))

    await jump_to(hass, freezer, first)
    await settle(hass)
    assert state(hass).state == STATE_ARMED
    assert not calls["light_on"]
    assert events[-1]["type"] == "skipped"
    assert events[-1]["reason"] == "not_home"
    assert dt_util.parse_datetime(attr(hass, "next_fire")) == first + timedelta(days=1)

    # Manual trigger ignores presence.
    await call(hass, "trigger_now")
    await settle(hass)
    assert state(hass).state == STATE_RISING
    assert calls["light_on"]


async def test_change_time_while_armed(hass, entry) -> None:
    await arm_fast(hass, entry)
    await set_config(hass, time_of_day="06:30")
    next_fire = dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire")))
    assert next_fire.strftime("%H:%M") == "06:30"
    assert attr(hass, "time_of_day") == "06:30"


async def test_restore_settings_and_pending_snooze(hass, entry, freezer, calls) -> None:
    snooze_until = dt_util.utcnow() + timedelta(minutes=7)
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                STATE_SNOOZED,
                {
                    "time_of_day": "06:15",
                    "volume": 0.6,
                    "playlist": PLAYLISTS[1],
                    "weekdays": ["mon", "fri"],
                    "require_home": True,
                    "fade_duration": 20,
                    "fade_music_duration": 10,
                    "snooze_until": snooze_until.isoformat(),
                },
            )
        ],
    )
    await setup_entry(hass, entry)

    s = state(hass)
    assert s.state == STATE_SNOOZED
    assert s.attributes["time_of_day"] == "06:15"
    assert s.attributes["volume"] == 0.6
    assert s.attributes["playlist"] == PLAYLISTS[1]
    assert s.attributes["weekdays"] == ["mon", "fri"]
    assert s.attributes["require_home"] is True
    assert s.attributes["snooze_until"] == snooze_until.isoformat()

    await jump_to(hass, freezer, snooze_until)
    await settle(hass)
    assert state(hass).state == STATE_RISING
    assert len(calls["play_media"]) == 1


async def test_restore_interrupted_run_resumes_ringing(hass, entry, freezer, calls) -> None:
    run_started = dt_util.utcnow() - timedelta(minutes=3)
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                STATE_RINGING,
                {
                    "fade_duration": 20,
                    "auto_off_minutes": 30,
                    "run_started": run_started.isoformat(),
                },
            )
        ],
    )
    await setup_entry(hass, entry)
    await settle(hass)
    assert state(hass).state == STATE_RISING
    assert len(calls["play_media"]) == 1

    await call(hass, "stop")
    assert state(hass).state == STATE_ARMED
    assert len(calls["media_stop"]) == 1


async def test_restore_expired_run_just_rearms(hass, entry, calls) -> None:
    run_started = dt_util.utcnow() - timedelta(hours=3)
    mock_restore_cache(
        hass,
        [State(ENTITY_ID, STATE_RINGING, {"run_started": run_started.isoformat()})],
    )
    await setup_entry(hass, entry)
    await settle(hass)
    assert state(hass).state == STATE_ARMED
    assert not calls["play_media"]


async def test_snooze_ignored_when_idle(hass, entry, calls) -> None:
    await arm_fast(hass, entry)
    await call(hass, "snooze")
    assert state(hass).state == STATE_ARMED
    assert not calls["media_stop"]


async def test_unknown_playlist_rejected(hass, entry) -> None:
    await arm_fast(hass, entry)
    await set_config(hass, playlist="library://playlist/nope")
    assert attr(hass, "playlist") == PLAYLISTS[0]
    await set_config(hass, playlist=PLAYLISTS[1])
    assert attr(hass, "playlist") == PLAYLISTS[1]


async def test_unload_cancels_run_but_keeps_music(hass, entry, freezer, calls) -> None:
    await arm_fast(hass, entry)
    await fire_and_ring(hass, freezer)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "unavailable"
    assert not calls["media_stop"]
