"""Constants for the Personal Wakeup integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "personal_wakeup"

# Config entry options (static wiring chosen in the config / options flow)
CONF_LIGHT_ENTITY = "light_entity"
CONF_MA_PLAYER_ENTITY = "ma_player_entity"
CONF_PERSON_ENTITY = "person_entity"
CONF_REQUIRE_HOME = "require_home"
CONF_PLAYLIST_OPTIONS = "playlist_options"

# Services
SERVICE_SET_CONFIG = "set_config"
SERVICE_TRIGGER_NOW = "trigger_now"
SERVICE_SNOOZE = "snooze"
SERVICE_STOP = "stop"

# Runtime settings (service fields / restored attributes)
ATTR_ENABLED = "enabled"
ATTR_TIME_OF_DAY = "time_of_day"
ATTR_WEEKDAYS = "weekdays"
ATTR_SKIP_NEXT = "skip_next"
ATTR_FADE_DURATION = "fade_duration"
ATTR_FADE_MUSIC_DURATION = "fade_music_duration"
ATTR_VOLUME = "volume"
ATTR_PLAYLIST = "playlist"
ATTR_REQUIRE_HOME = "require_home"
ATTR_SNOOZE_MINUTES = "snooze_minutes"
ATTR_AUTO_OFF_MINUTES = "auto_off_minutes"
ATTR_DURATION_MINUTES = "duration_minutes"

# Read-only attributes
ATTR_PLAYLIST_OPTIONS = "playlist_options"
ATTR_NEXT_FIRE = "next_fire"
ATTR_SKIPPED_FIRE = "skipped_fire"
ATTR_SNOOZE_UNTIL = "snooze_until"
ATTR_RUN_STARTED = "run_started"
ATTR_PERSON_ENTITY = "person_entity"
ATTR_LIGHT_ENTITY = "light_entity"
ATTR_PLAYER_ENTITY = "player_entity"
ATTR_CAN_SNOOZE = "can_snooze"
ATTR_CAN_STOP = "can_stop"

# Entity states
STATE_DISARMED = "disarmed"
STATE_ARMED = "armed"
STATE_RISING = "rising"  # light / music fading in
STATE_RINGING = "ringing"  # fully on, waiting for the user
STATE_SNOOZED = "snoozed"

ACTIVE_STATES = {STATE_RISING, STATE_RINGING}
STOPPABLE_STATES = {STATE_RISING, STATE_RINGING, STATE_SNOOZED}

# Events: hass.bus event type is EVENT_TYPE, payload has "type" set to one of EVENT_*
EVENT_TYPE = f"{DOMAIN}_event"
EVENT_TRIGGERED = "triggered"
EVENT_RINGING = "ringing"
EVENT_SNOOZED = "snoozed"
EVENT_STOPPED = "stopped"
EVENT_SKIPPED = "skipped"
EVENT_AUTO_OFF = "auto_off"

# Defaults
DEFAULT_TIME_OF_DAY = "07:00"
DEFAULT_FADE_DURATION = 900  # seconds
DEFAULT_FADE_MUSIC_DURATION = 300  # seconds
DEFAULT_VOLUME = 0.25
DEFAULT_SNOOZE_MINUTES = 10
DEFAULT_AUTO_OFF_MINUTES = 60
DEFAULT_ENABLED = True

# Fade behaviour
FADE_STEP_SECONDS = 5
RESUME_RAMP_SECONDS = 30  # music ramp after a snooze

PLATFORMS: list[Platform] = [Platform.SENSOR]
