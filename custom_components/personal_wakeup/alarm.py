"""Wakeup alarm entity: scheduling, run state machine and fades."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.components.media_player.const import ATTR_MEDIA_VOLUME_LEVEL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_STOP,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_HOME,
    STATE_ON,
    WEEKDAYS,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_STATES,
    ATTR_AUTO_OFF_MINUTES,
    ATTR_CAN_SNOOZE,
    ATTR_CAN_STOP,
    ATTR_DAY_TIMES,
    ATTR_ENABLED,
    ATTR_FADE_DURATION,
    ATTR_FADE_MUSIC_DURATION,
    ATTR_FADE_START,
    ATTR_LIGHT_ENTITY,
    ATTR_NEXT_FIRE,
    ATTR_PERSON_ENTITY,
    ATTR_PLAYER_ENTITY,
    ATTR_PLAYLIST,
    ATTR_PLAYLIST_OPTIONS,
    ATTR_REQUIRE_HOME,
    ATTR_RUN_STARTED,
    ATTR_SKIP_NEXT,
    ATTR_SKIPPED_FIRE,
    ATTR_SNOOZE_MINUTES,
    ATTR_SNOOZE_UNTIL,
    ATTR_TIME_OF_DAY,
    ATTR_VOLUME,
    ATTR_WAKE_AT,
    ATTR_WAKE_BRIGHTNESS,
    ATTR_WEEKDAYS,
    CONF_LIGHT_ENTITY,
    CONF_MA_PLAYER_ENTITY,
    CONF_PERSON_ENTITIES,
    CONF_PERSON_ENTITY,
    CONF_PLAYLIST_OPTIONS,
    CONF_REQUIRE_HOME,
    CONF_WAKE_MODE,
    DEFAULT_AUTO_OFF_MINUTES,
    DEFAULT_ENABLED,
    DEFAULT_FADE_DURATION,
    DEFAULT_FADE_MUSIC_DURATION,
    DEFAULT_SNOOZE_MINUTES,
    DEFAULT_TIME_OF_DAY,
    DEFAULT_VOLUME,
    DEFAULT_WAKE_BRIGHTNESS,
    DOMAIN,
    EVENT_AUTO_OFF,
    EVENT_COMPLETED,
    EVENT_RINGING,
    EVENT_SKIPPED,
    EVENT_SNOOZED,
    EVENT_STOPPED,
    EVENT_TRIGGERED,
    EVENT_TYPE,
    FADE_STEP_SECONDS,
    RESUME_RAMP_SECONDS,
    STATE_ARMED,
    STATE_DISARMED,
    STATE_RINGING,
    STATE_RISING,
    STATE_SNOOZED,
    STOPPABLE_STATES,
)
from .utils import selected_people, validate_day_times, validate_wiring

_LOGGER = logging.getLogger(__name__)

MAX_BRIGHTNESS = 255


def _parse_time(raw: Any) -> time | None:
    """Parse a time object or 'HH:MM[:SS]' string into a minute-precision time."""
    if isinstance(raw, time):
        return raw.replace(second=0, microsecond=0)
    parts = str(raw).strip().split(":")
    if len(parts) < 2:
        return None
    hh, mm = int(parts[0]), int(parts[1])
    return time(hour=hh, minute=mm)


def _parse_weekdays(raw: Any) -> list[str] | None:
    """Normalise a weekday list; returns None if the value is unusable."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    if not isinstance(raw, (list, tuple, set)):
        return None
    days = [str(d).strip().lower()[:3] for d in raw]
    days = [d for d in days if d in WEEKDAYS]
    # keep canonical order, drop duplicates
    return [d for d in WEEKDAYS if d in days]


@dataclass
class WakeupConfig:
    """User-adjustable runtime settings (persisted via restore state)."""

    enabled: bool = DEFAULT_ENABLED
    time_of_day: time = field(default_factory=lambda: _parse_time(DEFAULT_TIME_OF_DAY))
    weekdays: list[str] = field(default_factory=lambda: list(WEEKDAYS))
    day_times: dict[str, str] = field(default_factory=dict)
    skip_next: bool = False
    fade_duration: int = DEFAULT_FADE_DURATION  # seconds
    fade_music_duration: int = DEFAULT_FADE_MUSIC_DURATION  # seconds
    volume: float = DEFAULT_VOLUME
    playlist: str = ""
    require_home: bool = False
    snooze_minutes: int = DEFAULT_SNOOZE_MINUTES
    auto_off_minutes: int = DEFAULT_AUTO_OFF_MINUTES
    wake_brightness: int = DEFAULT_WAKE_BRIGHTNESS  # percent at the alarm time


class WakeupAlarmEntity(RestoreEntity, Entity):
    """Single wakeup alarm entity for one config entry.

    The alarm time is the wake time: the light's sunrise runs for
    fade_duration before it and reaches wake_brightness at it; music starts
    at it.

    State machine:

        disarmed  -> armed      (enabled)
        armed     -> rising     (fade start; light fades in, then music)
        rising    -> ringing    (fades complete, waits for the user)
        rising    -> armed      (lights only: the light reached its level)
        ringing   -> armed      (stop, or auto-off after auto_off_minutes)
        rising/ringing -> snoozed   (snooze, not lights only; music stops)
        snoozed   -> ringing    (snooze time; music resumes at once, light on)
        snoozed   -> armed      (stop)
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:alarm"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        # Retain old wiring until a changed options entry has stopped its run.
        self._options = dict(entry.options)

        # The entry name names a device; the entity takes that name, and its ID
        # is "<name>_wakeup" in any UI language (see suggested_object_id).
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "Wakeup",
            manufacturer="Personal WakeUp",
            model="Wakeup alarm",
        )
        # Stable identity: never derive this from user-editable options.
        self._attr_unique_id = entry.entry_id

        playlist_options = self._playlist_options()
        self._config = WakeupConfig(
            playlist=playlist_options[0] if playlist_options else "",
            require_home=bool(entry.options.get(CONF_REQUIRE_HOME, False)),
        )

        self._state: str = STATE_DISARMED
        self._next_fire: datetime | None = None
        self._skipped_fire: datetime | None = None
        self._snooze_until: datetime | None = None
        self._run_started: datetime | None = None
        # The wake time of the current (or last) run, so a stopped run's
        # occurrence is not picked up again while it is still in the future.
        self._wake_at: datetime | None = None

        self._unsub_timer: CALLBACK_TYPE | None = None
        self._run_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Entity API
    # ------------------------------------------------------------------ #

    @property
    def suggested_object_id(self) -> str:
        # Prefixed with the device (entry) name: "Lila" -> sensor.lila_wakeup.
        return "wakeup"

    @property
    def state(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cfg = self._config
        return {
            ATTR_ENABLED: cfg.enabled,
            ATTR_TIME_OF_DAY: cfg.time_of_day.isoformat(timespec="minutes"),
            ATTR_WEEKDAYS: list(cfg.weekdays),
            ATTR_DAY_TIMES: dict(cfg.day_times),
            CONF_PERSON_ENTITIES: self._person_entities,
            CONF_WAKE_MODE: self._wake_mode,
            ATTR_SKIP_NEXT: cfg.skip_next,
            ATTR_FADE_DURATION: cfg.fade_duration,
            ATTR_FADE_MUSIC_DURATION: cfg.fade_music_duration,
            ATTR_VOLUME: cfg.volume,
            ATTR_PLAYLIST: cfg.playlist,
            ATTR_PLAYLIST_OPTIONS: self._playlist_options(),
            ATTR_REQUIRE_HOME: cfg.require_home,
            ATTR_SNOOZE_MINUTES: cfg.snooze_minutes,
            ATTR_AUTO_OFF_MINUTES: cfg.auto_off_minutes,
            ATTR_WAKE_BRIGHTNESS: cfg.wake_brightness,
            ATTR_NEXT_FIRE: self._next_fire.isoformat() if self._next_fire else None,
            ATTR_FADE_START: (
                self._fade_start(self._next_fire).isoformat()
                if self._next_fire and self._state == STATE_ARMED
                else None
            ),
            ATTR_WAKE_AT: self._wake_at.isoformat() if self._wake_at else None,
            ATTR_SKIPPED_FIRE: (self._skipped_fire.isoformat() if self._skipped_fire else None),
            ATTR_SNOOZE_UNTIL: (self._snooze_until.isoformat() if self._snooze_until else None),
            ATTR_RUN_STARTED: (self._run_started.isoformat() if self._run_started else None),
            ATTR_PERSON_ENTITY: self._person_entity,
            ATTR_LIGHT_ENTITY: self._light_entity,
            ATTR_PLAYER_ENTITY: self._player_entity,
            # A light alone cannot be snoozed; once it is up, there is
            # nothing left to stop.
            ATTR_CAN_SNOOZE: self._state in STOPPABLE_STATES and self._wake_mode != "lights",
            ATTR_CAN_STOP: (
                self._state == STATE_RISING
                if self._wake_mode == "lights"
                else self._state in STOPPABLE_STATES
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            await self._reschedule()
            self._write()
            return

        attrs = dict(last_state.attributes)
        self._apply_runtime_settings(attrs)
        await self._restore_run_state(last_state.state, attrs)
        self._write()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel timers and the run task. Playback is left alone on unload."""
        self._cancel_timer()
        await self._cancel_run()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @property
    def _person_entities(self) -> list[str]:
        return selected_people(self._options)

    @property
    def _person_entity(self) -> str | None:
        people = self._person_entities
        return people[0] if people else None

    @property
    def _wake_mode(self) -> str:
        return self._options.get(CONF_WAKE_MODE, "both")

    @property
    def _lead_seconds(self) -> int:
        """How long before the wake time a run starts: the light's sunrise."""
        if self._wake_mode == "music" or not self._light_entity:
            return 0
        return max(1, int(self._config.fade_duration))

    @property
    def _music_ramp_seconds(self) -> int:
        return 0 if self._wake_mode == "lights" else max(1, int(self._config.fade_music_duration))

    def _fade_start(self, wake_at: datetime) -> datetime:
        return wake_at - timedelta(seconds=self._lead_seconds)

    @property
    def _target_brightness(self) -> int:
        return max(1, round(MAX_BRIGHTNESS * self._config.wake_brightness / 100))

    @property
    def _light_entity(self) -> str | None:
        return self._options.get(CONF_LIGHT_ENTITY) or None

    @property
    def _player_entity(self) -> str | None:
        return self._options.get(CONF_MA_PLAYER_ENTITY) or None

    def _playlist_options(self) -> list[str]:
        raw = self._options.get(CONF_PLAYLIST_OPTIONS, [])
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _write(self) -> None:
        """Write state if the entity has been added to hass."""
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    def _set_state(self, new_state: str) -> None:
        if new_state != self._state:
            _LOGGER.debug("%s: %s -> %s", self.entity_id, self._state, new_state)
        self._state = new_state
        self._write()

    def _fire_event(self, event_type: str, **extra: Any) -> None:
        self.hass.bus.async_fire(
            EVENT_TYPE,
            {"entity_id": self.entity_id, "type": event_type, **extra},
        )

    def _apply_runtime_settings(self, data: dict[str, Any]) -> None:
        """Apply runtime settings from service data or restored attributes."""
        cfg = self._config

        if ATTR_DAY_TIMES in data:
            try:
                cfg.day_times = validate_day_times(data[ATTR_DAY_TIMES])
            except vol.Invalid:
                _LOGGER.warning("Ignoring invalid restored day_times for %s", self.entity_id)

        if ATTR_ENABLED in data:
            cfg.enabled = bool(data[ATTR_ENABLED])

        if ATTR_TIME_OF_DAY in data:
            try:
                parsed = _parse_time(data[ATTR_TIME_OF_DAY])
            except (TypeError, ValueError):
                parsed = None
            if parsed is None:
                _LOGGER.error(
                    "Invalid time_of_day %r for %s", data[ATTR_TIME_OF_DAY], self.entity_id
                )
            else:
                cfg.time_of_day = parsed

        if ATTR_WEEKDAYS in data:
            days = _parse_weekdays(data[ATTR_WEEKDAYS])
            if days is None:
                _LOGGER.error("Invalid weekdays %r for %s", data[ATTR_WEEKDAYS], self.entity_id)
            else:
                # An empty selection means "every day".
                cfg.weekdays = days or list(WEEKDAYS)

        if ATTR_SKIP_NEXT in data:
            cfg.skip_next = bool(data[ATTR_SKIP_NEXT])

        for key in (
            ATTR_FADE_DURATION,
            ATTR_FADE_MUSIC_DURATION,
            ATTR_SNOOZE_MINUTES,
            ATTR_AUTO_OFF_MINUTES,
        ):
            if key in data:
                try:
                    setattr(cfg, key, max(1, int(data[key])))
                except (TypeError, ValueError):
                    _LOGGER.error("Invalid %s %r for %s", key, data[key], self.entity_id)

        if ATTR_WAKE_BRIGHTNESS in data:
            try:
                cfg.wake_brightness = max(1, min(100, int(data[ATTR_WAKE_BRIGHTNESS])))
            except (TypeError, ValueError):
                _LOGGER.error(
                    "Invalid wake_brightness %r for %s",
                    data[ATTR_WAKE_BRIGHTNESS],
                    self.entity_id,
                )

        if ATTR_VOLUME in data:
            try:
                cfg.volume = max(0.0, min(1.0, float(data[ATTR_VOLUME])))
            except (TypeError, ValueError):
                _LOGGER.error("Invalid volume %r for %s", data[ATTR_VOLUME], self.entity_id)

        if ATTR_PLAYLIST in data:
            playlist = str(data[ATTR_PLAYLIST] or "").strip()
            options = self._playlist_options()
            if options and playlist and playlist not in options:
                _LOGGER.warning("Ignoring unknown playlist %r for %s", playlist, self.entity_id)
            elif options and not playlist:
                cfg.playlist = options[0]
            else:
                cfg.playlist = playlist

        if ATTR_REQUIRE_HOME in data:
            cfg.require_home = bool(data[ATTR_REQUIRE_HOME])

    async def _restore_run_state(self, last_state: str, attrs: dict[str, Any]) -> None:
        """Re-establish a pending snooze or an interrupted run after a restart."""
        now = dt_util.utcnow()
        # Before 0.6.0 a run started at the alarm time, so its start is its wake time.
        wake_at = dt_util.parse_datetime(
            str(attrs.get(ATTR_WAKE_AT) or attrs.get(ATTR_RUN_STARTED) or "")
        )
        self._wake_at = dt_util.as_utc(wake_at) if wake_at else None

        if last_state == STATE_SNOOZED and self._config.enabled:
            snooze_until = dt_util.parse_datetime(str(attrs.get(ATTR_SNOOZE_UNTIL) or ""))
            if snooze_until is not None:
                snooze_until = dt_util.as_utc(snooze_until)
                if snooze_until > now:
                    _LOGGER.info("%s: restoring snooze until %s", self.entity_id, snooze_until)
                    self._schedule_snooze_at(snooze_until)
                    return
                # Snooze time passed while we were down: resume ringing now.
                await self._start_run(resume=True, ignore_presence=True)
                return

        if last_state in ACTIVE_STATES and self._config.enabled and self._wake_at:
            run_started = dt_util.parse_datetime(str(attrs.get(ATTR_RUN_STARTED) or ""))
            run_started = dt_util.as_utc(run_started) if run_started else now
            if now < self._wake_at:
                # Still before the wake time: carry on with the sunrise.
                _LOGGER.info("%s: resuming interrupted sunrise", self.entity_id)
                await self._start_run(
                    resume=False,
                    ignore_presence=True,
                    wake_at=self._wake_at,
                    run_started=run_started,
                )
                return
            deadline = self._wake_at + timedelta(
                seconds=self._music_ramp_seconds + self._config.auto_off_minutes * 60
            )
            remaining = (deadline - now).total_seconds()
            if remaining > 0 and self._wake_mode != "lights":
                _LOGGER.info(
                    "%s: resuming interrupted alarm run (%.0fs until auto-off)",
                    self.entity_id,
                    remaining,
                )
                await self._start_run(
                    resume=True,
                    ignore_presence=True,
                    auto_off_seconds=remaining,
                    run_started=run_started,
                    wake_at=self._wake_at,
                )
                return
            if self._wake_mode == "lights":
                # The sunrise was over: make sure the light got there.
                await self._ensure_light_on()

        await self._reschedule()

    # ------------------------------------------------------------------ #
    # Scheduling
    # ------------------------------------------------------------------ #

    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    def _next_occurrences(self, now_local: datetime, count: int) -> list[datetime]:
        """Return the next `count` occurrences (UTC) matching time_of_day and weekdays."""
        cfg = self._config
        found: list[datetime] = []
        for offset in range(0, 7 * count + 1):
            day = now_local.date() + timedelta(days=offset)
            day_time = (
                _parse_time(cfg.day_times[WEEKDAYS[day.weekday()]])
                if WEEKDAYS[day.weekday()] in cfg.day_times
                else cfg.time_of_day
            )
            candidate = datetime.combine(day, day_time, tzinfo=now_local.tzinfo)
            if candidate <= now_local:
                continue
            # A run already handled this wake time (stopped, skipped or done).
            if self._wake_at is not None and dt_util.as_utc(candidate) <= self._wake_at:
                continue
            if WEEKDAYS[candidate.weekday()] not in cfg.weekdays:
                continue
            found.append(dt_util.as_utc(candidate))
            if len(found) >= count:
                break
        return found

    async def _reschedule(self) -> None:
        """Arm the next regular occurrence (or disarm). Does not touch a run."""
        self._cancel_timer()
        self._snooze_until = None
        self._skipped_fire = None

        if not self._config.enabled:
            self._next_fire = None
            self._set_state(STATE_DISARMED)
            return

        occurrences = self._next_occurrences(dt_util.now(), 2)
        if not occurrences:
            self._next_fire = None
            self._set_state(STATE_ARMED)
            return

        # The run starts the fade before the wake time; if that moment has
        # passed (an alarm set close to its time), it starts now, shorter.
        timer_at = max(self._fade_start(occurrences[0]), dt_util.utcnow())
        if self._config.skip_next:
            self._skipped_fire = occurrences[0]
            self._next_fire = occurrences[1] if len(occurrences) > 1 else None
        else:
            self._next_fire = occurrences[0]

        _LOGGER.info(
            "%s armed: timer=%s next_fire=%s skipped=%s",
            self.entity_id,
            timer_at,
            self._next_fire,
            self._skipped_fire,
        )
        self._unsub_timer = async_track_point_in_utc_time(self.hass, self._on_alarm_time, timer_at)
        self._set_state(STATE_ARMED)

    @callback
    def _on_alarm_time(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.hass.async_create_task(self._handle_alarm_time())

    async def _handle_alarm_time(self) -> None:
        wake_at = self._skipped_fire if self._config.skip_next else self._next_fire
        if self._config.skip_next:
            skipped = self._skipped_fire
            self._config.skip_next = False
            self._wake_at = skipped
            _LOGGER.info("%s: skipping occurrence %s", self.entity_id, skipped)
            self._fire_event(
                EVENT_SKIPPED,
                reason="skip_next",
                fire_time=skipped.isoformat() if skipped else None,
            )
            await self._reschedule()
            self._write()
            return

        await self._start_run(resume=False, ignore_presence=False, wake_at=wake_at)

    def _schedule_snooze_at(self, snooze_until: datetime) -> None:
        self._cancel_timer()
        self._snooze_until = snooze_until
        self._next_fire = snooze_until
        self._skipped_fire = None
        self._unsub_timer = async_track_point_in_utc_time(
            self.hass, self._on_snooze_time, snooze_until
        )
        self._set_state(STATE_SNOOZED)

    @callback
    def _on_snooze_time(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.hass.async_create_task(self._start_run(resume=True, ignore_presence=True))

    # ------------------------------------------------------------------ #
    # Run lifecycle
    # ------------------------------------------------------------------ #

    def _person_is_home(self) -> bool:
        return not self._person_entities or any(
            (person_state := self.hass.states.get(person)) is not None
            and person_state.state == STATE_HOME
            for person in self._person_entities
        )

    async def _start_run(
        self,
        *,
        resume: bool,
        ignore_presence: bool,
        auto_off_seconds: float | None = None,
        run_started: datetime | None = None,
        wake_at: datetime | None = None,
    ) -> None:
        """Start (or restart) the alarm run as a background task.

        `wake_at` is when the light reaches its level and music starts; a
        manual trigger or a resume without one wakes after a full fade.
        """
        self._cancel_timer()
        await self._cancel_run()
        if wake_at is None:
            wake_at = dt_util.utcnow() + timedelta(seconds=0 if resume else self._lead_seconds)
        self._wake_at = wake_at

        if not ignore_presence and self._config.require_home and not self._person_is_home():
            _LOGGER.info(
                "%s: skipping alarm because %s is not home",
                self.entity_id,
                self._person_entity,
            )
            self._fire_event(EVENT_SKIPPED, reason="not_home")
            await self._reschedule()
            self._write()
            return

        self._snooze_until = None
        self._run_started = run_started or dt_util.utcnow()
        self._run_task = self._entry.async_create_background_task(
            self.hass,
            self._run(resume=resume, auto_off_seconds=auto_off_seconds, wake_at=wake_at),
            name=f"{DOMAIN} run {self.entity_id}",
        )

    async def _cancel_run(self) -> None:
        task = self._run_task
        self._run_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(
        self, *, resume: bool, auto_off_seconds: float | None, wake_at: datetime
    ) -> None:
        """The alarm sequence: sunrise up to the wake time, then ring until stopped."""
        try:
            self._set_state(STATE_RISING)
            self._fire_event(EVENT_TRIGGERED, resume=resume, wake_at=wake_at.isoformat())

            try:
                # A restored deadline includes the resume ramp itself.
                async with asyncio.timeout(auto_off_seconds):
                    try:
                        if resume:
                            await asyncio.gather(
                                self._ensure_light_on(),
                                self._fade_music(resume=True),
                            )
                        else:
                            await asyncio.gather(
                                self._fade_light(wake_at), self._fade_music(wake_at=wake_at)
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - keep ringing even if a fade failed
                        _LOGGER.exception("%s: error during fade-in", self.entity_id)

                    if self._wake_mode == "lights":
                        # A light alone has nothing to ring: the sunrise is the alarm.
                        _LOGGER.info("%s: light reached its wake-up level", self.entity_id)
                        self._fire_event(EVENT_COMPLETED)
                        self._run_task = None
                        self._run_started = None
                        await self._reschedule()
                        self._write()
                        return

                    self._set_state(STATE_RINGING)
                    self._fire_event(EVENT_RINGING)

                    await asyncio.sleep(self._config.auto_off_minutes * 60)
            except TimeoutError:
                pass

            _LOGGER.info("%s: auto-off", self.entity_id)
            await self._stop_music()
            self._fire_event(EVENT_AUTO_OFF)
            self._run_task = None
            self._run_started = None
            await self._reschedule()
            self._write()
        finally:
            if self._run_task is asyncio.current_task():
                self._run_task = None

    # ------------------------------------------------------------------ #
    # Light
    # ------------------------------------------------------------------ #

    async def _light_turn_on(self, brightness: int, transition: float) -> None:
        await self.hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {
                ATTR_ENTITY_ID: self._light_entity,
                ATTR_BRIGHTNESS: brightness,
                ATTR_TRANSITION: transition,
            },
            blocking=False,
        )

    def _current_brightness(self) -> int:
        light_state = self.hass.states.get(self._light_entity or "")
        if light_state is None or light_state.state != STATE_ON:
            return 0
        try:
            return int(light_state.attributes.get(ATTR_BRIGHTNESS) or MAX_BRIGHTNESS)
        except (TypeError, ValueError):
            return 0

    async def _ensure_light_on(self) -> None:
        if self._wake_mode == "music" or not self._light_entity:
            return
        if self._current_brightness() >= self._target_brightness:
            return
        await self._light_turn_on(self._target_brightness, FADE_STEP_SECONDS)

    async def _fade_light(self, wake_at: datetime) -> None:
        """Sunrise: fade the light to wake_brightness, arriving at wake_at.

        Brightness follows the square of the elapsed share, because the eye is
        far more sensitive to changes when it is dark: a straight ramp looks
        bright within minutes, this one stays dim for most of the fade.
        """
        if self._wake_mode == "music":
            return
        if not self._light_entity:
            _LOGGER.warning("%s: no light configured; skipping light fade", self.entity_id)
            return

        target = self._target_brightness
        start = self._current_brightness()
        if start >= target:
            _LOGGER.info("%s: light already at its wake-up brightness", self.entity_id)
            return
        duration = max(0, int((wake_at - dt_util.utcnow()).total_seconds()))
        steps = max(1, duration // FADE_STEP_SECONDS)

        _LOGGER.info(
            "%s: sunrise on %s from %s to %s over %ss",
            self.entity_id,
            self._light_entity,
            start,
            target,
            duration,
        )
        for step in range(1, steps + 1):
            share = step / steps
            brightness = max(1, start + round((target - start) * share * share))
            await self._light_turn_on(brightness, FADE_STEP_SECONDS)
            if step < steps:
                await asyncio.sleep(FADE_STEP_SECONDS)

    # ------------------------------------------------------------------ #
    # Music
    # ------------------------------------------------------------------ #

    async def _set_volume(self, volume: float) -> None:
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_VOLUME_SET,
            {ATTR_ENTITY_ID: self._player_entity, ATTR_MEDIA_VOLUME_LEVEL: round(volume, 3)},
            blocking=False,
        )

    async def _play_music(self) -> None:
        playlist = self._config.playlist
        if not playlist:
            _LOGGER.warning("%s: no playlist configured; skipping music", self.entity_id)
            return
        _LOGGER.info("%s: playing %r on %s", self.entity_id, playlist, self._player_entity)
        await self.hass.services.async_call(
            "music_assistant",
            "play_media",
            {
                ATTR_ENTITY_ID: self._player_entity,
                "media_id": playlist,
                "media_type": "playlist",
                "enqueue": "replace",
            },
            blocking=True,
        )

    async def _stop_music(self) -> None:
        """Stop playback. Blocking so it can never be overtaken by a queued play."""
        if self._wake_mode == "lights" or not self._player_entity:
            return
        try:
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_MEDIA_STOP,
                {ATTR_ENTITY_ID: self._player_entity},
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("%s: error stopping %s: %s", self.entity_id, self._player_entity, exc)

    async def _fade_music(self, *, resume: bool = False, wake_at: datetime | None = None) -> None:
        """Start playback and ramp volume to the target.

        Regular run: playback starts at the wake time, when the light has
        reached its level, and ramps over fade_music_duration.
        Resume (after a snooze): playback starts immediately with a short ramp.
        """
        if self._wake_mode == "lights":
            return
        if not self._player_entity:
            _LOGGER.warning("%s: no media player configured; skipping music", self.entity_id)
            return

        target = max(0.0, min(1.0, float(self._config.volume)))
        if resume:
            ramp = RESUME_RAMP_SECONDS
            delay = 0.0
        else:
            ramp = max(1, int(self._config.fade_music_duration))
            delay = max(0.0, (wake_at - dt_util.utcnow()).total_seconds()) if wake_at else 0.0

        if delay > 0:
            _LOGGER.debug("%s: music starts in %.0fs", self.entity_id, delay)
            await asyncio.sleep(delay)

        steps = max(1, int(ramp) // FADE_STEP_SECONDS)
        _LOGGER.info(
            "%s: music ramp to %.2f over %ss in %s steps", self.entity_id, target, ramp, steps
        )
        try:
            await self._set_volume(0.0)
            await self._play_music()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("%s: error starting playback: %s", self.entity_id, exc)
            return

        for step in range(1, steps + 1):
            await self._set_volume(target * step / steps)
            if step < steps:
                await asyncio.sleep(FADE_STEP_SECONDS)

    # ------------------------------------------------------------------ #
    # Service entry points
    # ------------------------------------------------------------------ #

    async def async_set_config(self, **data: Any) -> None:
        """Update runtime settings from the set_config service."""
        _LOGGER.debug("%s: set_config %s", self.entity_id, data)
        device_options = {
            key: data[key]
            for key in (
                CONF_LIGHT_ENTITY,
                CONF_MA_PLAYER_ENTITY,
                CONF_PERSON_ENTITY,
                CONF_PERSON_ENTITIES,
                CONF_WAKE_MODE,
            )
            if key in data
        }
        if ATTR_DAY_TIMES in data:
            validate_day_times(data[ATTR_DAY_TIMES])
        if CONF_PERSON_ENTITIES in device_options:
            device_options[CONF_PERSON_ENTITIES] = selected_people(device_options)
        elif CONF_PERSON_ENTITY in device_options:
            device_options[CONF_PERSON_ENTITIES] = selected_people(device_options)
        validate_wiring({**self._options, **device_options})
        if device_options:
            # Options reload the entity; stop the original player before replacing it.
            if self._state in STOPPABLE_STATES:
                await self.async_stop()
        was_enabled = self._config.enabled
        self._apply_runtime_settings(data)

        if was_enabled and not self._config.enabled:
            await self._disarm()
        elif self._state in (STATE_DISARMED, STATE_ARMED):
            # Re-arm with the new settings. A pending snooze or an active run
            # is deliberately left alone; the new settings apply afterwards.
            await self._reschedule()
        self._write()
        if device_options:
            self.hass.config_entries.async_update_entry(
                self._entry, options={**self._options, **device_options}
            )

    async def async_prepare_options_reload(self) -> None:
        """Stop the old enabled player before changed options are reloaded."""
        if dict(self._entry.options) != self._options and self._state in STOPPABLE_STATES:
            await self.async_stop()

    async def _disarm(self) -> None:
        """Disable: cancel everything, stop playback if we were making noise."""
        was_active = self._state in ACTIVE_STATES
        self._cancel_timer()
        await self._cancel_run()
        if was_active:
            await self._stop_music()
            self._fire_event(EVENT_STOPPED, reason="disabled")
        self._run_started = None
        await self._reschedule()

    async def async_trigger(self) -> None:
        """Start the wakeup sequence now, ignoring presence."""
        _LOGGER.info("%s: manual trigger", self.entity_id)
        await self._start_run(resume=False, ignore_presence=True)

    async def async_snooze(self, duration_minutes: int | None = None) -> None:
        """Silence the alarm and ring again after duration_minutes."""
        if self._wake_mode == "lights":
            _LOGGER.info("%s: snooze ignored, a light alone cannot be snoozed", self.entity_id)
            return
        if self._state not in STOPPABLE_STATES:
            _LOGGER.info("%s: snooze ignored, alarm not active", self.entity_id)
            return

        minutes = max(1, int(duration_minutes or self._config.snooze_minutes))
        _LOGGER.info("%s: snoozing for %s min", self.entity_id, minutes)

        await self._cancel_run()
        await self._stop_music()
        snooze_until = dt_util.utcnow() + timedelta(minutes=minutes)
        self._schedule_snooze_at(snooze_until)
        self._fire_event(EVENT_SNOOZED, minutes=minutes, snooze_until=snooze_until.isoformat())
        self._write()

    async def async_stop(self) -> None:
        """Stop the alarm (or cancel a snooze) and arm the next occurrence."""
        was_active = self._state in STOPPABLE_STATES
        _LOGGER.info("%s: stop (active=%s)", self.entity_id, was_active)

        await self._cancel_run()
        if was_active:
            await self._stop_music()
            self._fire_event(EVENT_STOPPED, reason="user")
        self._run_started = None
        await self._reschedule()
        self._write()

    def debug_config(self) -> dict[str, Any]:
        """Config snapshot for logging and tests."""
        return asdict(self._config)
