<img src="custom_components/personal_wakeup/brand/icon.png" alt="" width="96" align="right">

# Personal Wakeup (Home Assistant integration)

A wake-up alarm for Home Assistant: a sunrise-style light fade, a Music
Assistant playlist that fades in, and a real alarm-clock lifecycle with
**stop**, **snooze**, individual daily times, skip-next, any-person-home presence check and auto-off.
Choose **lights**, **music**, or **both** for each alarm.

Pair it with the [Lovelace Personal Wakeup Card](https://github.com/mvheimburg/lovelace-personal-wakeup)
for a one-tap Stop / Snooze UI.

## How an alarm runs

```
disarmed ─enable─▶ armed ─alarm time─▶ rising ─fades done─▶ ringing
                     ▲                    │                    │
                     │      stop / auto-off (default 60 min)   │
                     ├────────────────────┴────────────────────┤
                     │                                         │
                     │ stop                snooze              ▼
                     └──────── snoozed ◀────────────────── (rising/ringing)
                                  │ snooze time
                                  └──▶ rising ─▶ ringing   (music at once, light stays)
```

- **armed**: waiting for the next occurrence (`next_fire`).
- **rising**: the light fades from its current brightness to 100% over
  `fade_duration`. The music starts near the end of the light fade and ramps
  to `volume` over `fade_music_duration`, centred on the moment the light
  reaches full brightness.
- **ringing**: light and music are fully on. The alarm stays here until you
  stop it, snooze it, or `auto_off_minutes` elapse.
- **snoozed**: music is stopped, the light is left as it is. When the snooze
  ends the music comes back within 30 seconds and the light is switched to
  full if it was turned off.
- **stop** stops the music, leaves the light on, and arms the next occurrence.

`time_of_day` is when the light starts fading, so with the default 15 minute
fade a 07:00 alarm is fully on at 07:15.

A pending snooze and an interrupted run survive a Home Assistant restart.

## Installation (HACS)

1. Add this repository as a custom repository in HACS (category *Integration*).
2. Install **Personal Wakeup** and restart Home Assistant.
3. Add the integration from **Settings → Devices & services**.

The config flow asks for a wake mode, the targets used by that mode (a light
and/or Music Assistant player), optional people, the initial "only when home" value and a
comma-separated list of playlist URIs the card can pick from. Everything else
is a runtime setting changed from the card or the `set_config` service and is
restored across restarts.

One sensor entity is created per config entry, named after the name entered
during setup. For example, `Weekend wakeup` creates `sensor.weekend_wakeup`;
the selected people only control presence checks. Existing entity IDs remain
unchanged when updating the integration or renaming an entry. A custom name
set directly on the entity still takes precedence. Its state is
one of `disarmed`, `armed`, `rising`, `ringing`, `snoozed`.

## Services

All services target the sensor entity (`entity_id`, or device / area targets).

| Service | Fields | What it does |
| --- | --- | --- |
| `personal_wakeup.set_config` | `enabled`, `time_of_day`, `day_times`, `weekdays`, `skip_next`, `fade_duration`, `fade_music_duration`, `volume`, `playlist`, `require_home`, `snooze_minutes`, `auto_off_minutes`, `light_entity`, `ma_player_entity`, `person_entity`, `person_entities`, `wake_mode` | Update settings. Disabling stops an active alarm. Changing mode, device or person selections stops an active alarm or pending snooze and saves the selection in integration options. Other changes preserve a pending snooze. |
| `personal_wakeup.trigger_now` | | Start the wake-up sequence now, ignoring presence. |
| `personal_wakeup.snooze` | `duration_minutes` (optional, default `snooze_minutes`) | Silence and ring again later. Only while rising, ringing or snoozed. |
| `personal_wakeup.stop` | | Stop the alarm or cancel a snooze, arm the next occurrence. |

`weekdays` is a list of `mon` … `sun`. An empty list means every day.
`skip_next` skips exactly one occurrence and then clears itself, including when
only one weekday is enabled.

`day_times` maps weekday keys to local `HH:MM` strings, for example
`{"mon": "06:30", "fri": "07:15", "sat": "09:00"}`. Missing keys use
`time_of_day`; `{}` clears all overrides. Disabled weekdays retain their times.
The mapping is replaced as a whole on each update, and invalid keys/times reject
the whole service call before any settings change.

`person_entities` is an ordered list of person IDs. With `require_home` enabled,
any selected person whose state is exactly `home` permits a scheduled alarm.
Missing, unknown and unavailable people do not count as home. An empty list
leaves presence unrestricted. Manual triggers and snooze resumes bypass presence.
Existing `person_entity` options remain a fallback until a new list is saved;
the legacy service field replaces the list with one person (or clears it with
an empty string). If both fields are sent, `person_entities` wins. The sensor's
`person_entity` attribute remains a first-person compatibility alias.

`wake_mode` defaults to `both` for existing alarms. `lights` requires only a
light and makes no media calls; `music` requires only a player and makes no
light calls. Music-only playback begins immediately. Inactive targets may be
retained or cleared with an empty string. Send a mode change and its required
targets together in one `set_config` call. Invalid combinations leave the old
settings intact. Changing wiring or mode stops the old enabled player before
saving and rearming the alarm. Disabled channels stay untouched during snooze,
resume, stop and auto-off too.

These configuration fields require integration and card version **0.4.0**.

## Attributes

`enabled`, `time_of_day`, `day_times`, `weekdays`, `skip_next`, `fade_duration`,
`fade_music_duration`, `volume`, `playlist`, `playlist_options`,
`require_home`, `snooze_minutes`, `auto_off_minutes`, `next_fire`,
`skipped_fire`, `snooze_until`, `run_started`, `person_entity`, `person_entities`, `wake_mode`,
`light_entity`, `player_entity`, `can_snooze`, `can_stop`.

## Events

Every transition fires a `personal_wakeup_event` on the event bus with
`entity_id` and `type`:

| `type` | Extra data | When |
| --- | --- | --- |
| `triggered` | `resume` | A run starts (`resume: true` after a snooze) |
| `ringing` | | Fades are complete |
| `snoozed` | `minutes`, `snooze_until` | Snooze pressed |
| `stopped` | `reason`: `user` or `disabled` | Stop pressed or alarm disabled while active |
| `auto_off` | | Auto-off timeout reached |
| `skipped` | `reason`: `skip_next` or `not_home`, `fire_time` | An occurrence was skipped |

Example automation: start the coffee machine when the alarm is stopped.

```yaml
triggers:
  - trigger: event
    event_type: personal_wakeup_event
    event_data:
      entity_id: sensor.matilde_wakeup
      type: stopped
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.coffee_machine
```

## Development

```bash
pip install -r requirements_test.txt
pytest
ruff check custom_components tests
```

Releases are automatic: bump `version` in both `pyproject.toml` and
`custom_components/personal_wakeup/manifest.json`, merge to `main`, and the
release workflow tags `v<version>` and publishes a GitHub release. CI fails if
the two versions differ.
