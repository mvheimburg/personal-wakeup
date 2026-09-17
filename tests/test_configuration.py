"""Configuration contracts exercised through Home Assistant services and flows."""

from datetime import timedelta

import pytest
import voluptuous as vol
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .conftest import (
    ENTITY_ID,
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


@pytest.fixture(autouse=True)
def freeze(freezer):
    freezer.move_to(START)


@pytest.mark.parametrize(
    "other,allowed",
    [
        ("home", True),
        ("not_home", False),
        ("unknown", False),
        ("unavailable", False),
        (None, False),
    ],
)
async def test_any_selected_person_home(hass, entry, freezer, calls, other, allowed):
    await setup_entry(hass, entry)
    await set_config(
        hass, person_entities=["person.absent", "person.other", "person.other"], require_home=True
    )
    if other:
        hass.states.async_set("person.other", other)
    assert attr(hass, "person_entities") == ["person.absent", "person.other"]
    assert attr(hass, "person_entity") == "person.absent"
    await jump_to(hass, freezer, dt_util.parse_datetime(attr(hass, "next_fire")))
    await settle(hass)
    assert bool(calls["light_on"]) is allowed


async def test_empty_people_overrides_legacy_and_is_unrestricted(hass, entry, freezer, calls):
    await setup_entry(hass, entry)
    await set_config(hass, person_entities=[], person_entity="person.absent", require_home=True)
    assert attr(hass, "person_entities") == []
    assert attr(hass, "person_entity") is None
    await jump_to(hass, freezer, dt_util.parse_datetime(attr(hass, "next_fire")))
    await settle(hass)
    assert calls["light_on"]
    await set_config(hass, person_entity="person.legacy")
    assert attr(hass, "person_entities") == ["person.legacy"]


@pytest.mark.parametrize("mode", ["lights", "music"])
async def test_channel_mode_entire_lifecycle(hass, entry, freezer, calls, mode):
    await setup_entry(hass, entry)
    await set_config(
        hass, wake_mode=mode, fade_duration=900, fade_music_duration=5, auto_off_minutes=1
    )
    await call(hass, "trigger_now")
    await settle(hass)
    assert bool(calls["light_on"]) is (mode == "lights")
    assert bool(calls["play_media"]) is (mode == "music")
    await call(hass, "snooze", duration_minutes=1)
    await advance(hass, freezer, 60)
    await settle(hass)
    for _ in range(7):
        await advance(hass, freezer, 5)
    await advance(hass, freezer, 61)
    assert state(hass).state == "armed"
    await call(hass, "trigger_now")
    await call(hass, "stop")
    await call(hass, "trigger_now")
    await set_config(hass, enabled=False)
    if mode == "lights":
        assert not calls["play_media"] and not calls["volume_set"] and not calls["media_stop"]
    else:
        assert not calls["light_on"]


async def test_mode_targets_validate_merged_config_atomically(hass, entry, calls):
    await setup_entry(hass, entry)
    await set_config(hass, wake_mode="music", light_entity="")
    assert attr(hass, "wake_mode") == "music"
    with pytest.raises(vol.Invalid):
        await set_config(hass, wake_mode="both", volume=0.8)
    assert attr(hass, "wake_mode") == "music"
    assert attr(hass, "volume") == 0.25
    await call(hass, "trigger_now")
    await settle(hass)
    await set_config(hass, wake_mode="lights", light_entity="light.new", ma_player_entity="")
    assert calls["media_stop"][-1].data["entity_id"] == "media_player.bedroom"
    await call(hass, "trigger_now")
    await settle(hass)
    assert calls["light_on"][-1].data["entity_id"] == "light.new"


async def test_day_times_weekly_skip_and_clear(hass, entry):
    await setup_entry(hass, entry)
    await set_config(
        hass, weekdays=["mon"], day_times={"mon": "08:45", "sat": "10:30"}, skip_next=True
    )
    assert (
        dt_util.as_local(dt_util.parse_datetime(attr(hass, "skipped_fire"))).strftime(
            "%Y-%m-%d %H:%M"
        )
        == "2026-09-21 08:45"
    )
    assert (
        dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire"))).strftime("%Y-%m-%d %H:%M")
        == "2026-09-28 08:45"
    )
    await set_config(hass, day_times={}, skip_next=False)
    assert attr(hass, "day_times") == {}
    assert (
        dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire"))).strftime("%H:%M")
        == "07:00"
    )


@pytest.mark.parametrize(
    "bad", [{"monday": "08:00"}, {"mon": "25:00"}, {"tue": "x"}, {"sun": "09:15:59"}, []]
)
async def test_invalid_day_times_are_atomic(hass, entry, bad):
    await setup_entry(hass, entry)
    with pytest.raises(vol.Invalid):
        await set_config(hass, day_times=bad, enabled=False, person_entities=[])
    assert attr(hass, "enabled") is True
    assert attr(hass, "person_entity") == "person.matilde"


async def test_day_times_restore_and_default_fallback(hass, entry):
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                "armed",
                {"day_times": {"tue": "06:12", "wed": "08:03"}, "time_of_day": "07:10"},
            )
        ],
    )
    await setup_entry(hass, entry)
    assert attr(hass, "day_times") == {"tue": "06:12", "wed": "08:03"}
    assert (
        dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire"))).strftime("%H:%M")
        == "06:12"
    )
    await set_config(hass, weekdays=["thu"])
    assert (
        dt_util.as_local(dt_util.parse_datetime(attr(hass, "next_fire"))).strftime("%H:%M")
        == "07:10"
    )


async def test_music_restore_deadline_ignores_disabled_light_duration(hass, entry, calls):
    hass.config_entries.async_update_entry(entry, options={**entry.options, "wake_mode": "music"})
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                "ringing",
                {
                    "run_started": (dt_util.utcnow() - timedelta(minutes=3)).isoformat(),
                    "fade_duration": 900,
                    "fade_music_duration": 5,
                    "auto_off_minutes": 1,
                },
            )
        ],
    )
    await setup_entry(hass, entry)
    assert state(hass).state == "armed"
    assert not calls["play_media"]


@pytest.mark.parametrize(
    "mode,target",
    [
        ("lights", {"light_entity": "light.bed"}),
        ("music", {"ma_player_entity": "media_player.bed"}),
    ],
)
async def test_setup_flow_requires_only_enabled_target(hass, mode, target):
    result = await hass.config_entries.flow.async_init(
        "personal_wakeup", context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "New",
            "wake_mode": mode,
            "person_entities": ["person.a", "person.b"],
            "require_home": True,
            **target,
        },
    )
    assert result["type"] == "create_entry"
    assert result["options"]["person_entities"] == ["person.a", "person.b"]


async def test_options_flow_missing_target_keeps_form_and_inputs(hass, entry):
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Changed", "wake_mode": "music", "person_entities": ["person.other"]},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "no_player_entity"}
    defaults = {
        key.schema: key.default()
        for key in result["data_schema"].schema
        if key.default is not vol.UNDEFINED
    }
    assert defaults["wake_mode"] == "music"
    assert defaults["person_entities"] == ["person.other"]


async def test_options_mode_switch_stops_old_player(hass, entry, calls):
    await setup_entry(hass, entry)
    await call(hass, "trigger_now")
    await settle(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Renamed alarm",
            "wake_mode": "lights",
            "light_entity": "light.new",
            "person_entities": [],
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert calls["media_stop"][-1].data["entity_id"] == "media_player.bedroom"
    assert state(hass).state == "armed"
    await call(hass, "trigger_now")
    await settle(hass)
    assert calls["light_on"][-1].data["entity_id"] == "light.new"


async def test_restore_both_long_music_uses_full_music_duration(hass, entry, calls):
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                "ringing",
                {
                    "run_started": (dt_util.utcnow() - timedelta(minutes=4)).isoformat(),
                    "fade_duration": 5,
                    "fade_music_duration": 300,
                    "auto_off_minutes": 1,
                },
            )
        ],
    )
    await setup_entry(hass, entry)
    await settle(hass)
    assert calls["play_media"]


async def test_weekday_schedule_uses_local_date_across_utc_boundary(hass, entry):
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    await setup_entry(hass, entry)
    await set_config(hass, weekdays=["tue"], day_times={"tue": "00:15", "wed": "09:00"})
    assert attr(hass, "next_fire") == "2026-09-14T22:15:00+00:00"
    await set_config(hass, weekdays=["wed"])
    assert attr(hass, "day_times") == {"tue": "00:15", "wed": "09:00"}
    assert attr(hass, "next_fire") == "2026-09-16T07:00:00+00:00"


async def test_registry_custom_name_survives_entry_rename(hass, entry):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    registered = registry.async_get_or_create(
        "sensor",
        "personal_wakeup",
        entry.entry_id,
        suggested_object_id="custom_wakeup",
        config_entry=entry,
    )
    registry.async_update_entity(registered.entity_id, name="My bedside alarm")
    await setup_entry(hass, entry)
    hass.config_entries.async_update_entry(entry, title="New name")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.custom_wakeup").attributes["friendly_name"] == "My bedside alarm"


async def test_restore_auto_off_deadline_includes_resume_ramp(hass, entry, freezer, calls):
    hass.config_entries.async_update_entry(entry, options={**entry.options, "wake_mode": "music"})
    mock_restore_cache(
        hass,
        [
            State(
                ENTITY_ID,
                "ringing",
                {
                    "run_started": (dt_util.utcnow() - timedelta(seconds=55)).isoformat(),
                    "fade_music_duration": 5,
                    "auto_off_minutes": 1,
                },
            )
        ],
    )
    await setup_entry(hass, entry)
    await settle(hass)
    assert calls["play_media"]
    await advance(hass, freezer, 11)
    await settle(hass)
    assert state(hass).state == "armed"
    assert calls["media_stop"]
