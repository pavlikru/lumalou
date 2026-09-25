"""Command builders. Each returns the app-level payload: [opcode] + args."""

from __future__ import annotations

from ._generated import (
    COMMANDS,
    DAY_ROUTINE,
)
from .profile import (
    ClockSettings,
    MusicPlaylist,
    RoutineMusicSettings,
    encode_clock_settings,
    encode_music_playlist,
    encode_routine_music_settings,
)
from .schedules import (
    DailyRoutine,
    Day,
    WeeklyAlarms,
    WeeklyTimes,
    _integer,
    encode_daily_routine,
    encode_weekly_alarms,
    encode_weekly_times,
)

NO_MODIFY = 0x0F  # "leave unchanged" sentinel for SET_GLOBAL_STATE fields

_u8 = lambda *a: bytes(a)


def bcd(n) -> int:
    return 0xFF if n is None else (((n // 10) << 4) | (n % 10)) & 0xFF


def reduce_low_nibbles(values) -> bytes:
    v = list(values)
    if len(v) % 2:
        v = [0] + v
    return bytes(((v[i] << 4) | (v[i + 1] & 0x0F)) & 0xFF for i in range(0, len(v), 2))


# ---- light ----
# The deployed web client has no separate "light on" command: picking a colour
# (SET_LIGHT_COLOR) is its light-on action and "Turn light off" sends
# TURN_OFF_CLOUD_BACKLIGHT. A brightness write alone was observed on hardware
# not to switch the light on.
def set_light_color(color) -> bytes:
    return _u8(COMMANDS["SET_LIGHT_COLOR"], _integer(color, 0, 9, "color"))


def set_led_brightness(level: int) -> bytes:
    return _u8(COMMANDS["SET_LED_BRIGHTNESS"], _integer(level, 0, 9, "brightness"))


def set_light_duration(duration) -> bytes:
    return _u8(
        COMMANDS["SET_SOOTHER_MODE_LIGHT_DURATION"],
        _integer(duration, 0, 5, "light duration"),
    )


def turn_off_backlight() -> bytes:
    return _u8(COMMANDS["TURN_OFF_CLOUD_BACKLIGHT"])


# ---- audio ----
def play_audio(source) -> bytes:
    # Audio enum 0..7, as validated by the deployed web client.
    return _u8(COMMANDS["PLAY_AUDIO"], _integer(source, 0, 7, "audio source"))


def turn_off_audio() -> bytes:
    return _u8(COMMANDS["TURN_OFF_AUDIO"])


def set_playlist_duration(duration) -> bytes:
    return _u8(
        COMMANDS["SET_PLAYLIST_DURATION"], _integer(duration, 0, 6, "playlist duration")
    )


def set_music_playlist(song_ids) -> bytes:
    playlist = (
        song_ids
        if isinstance(song_ids, MusicPlaylist)
        else MusicPlaylist.from_songs(song_ids)
    )
    return _u8(COMMANDS["SET_MUSIC_PLAYLIST"]) + encode_music_playlist(playlist)


# ---- volume ----
def set_volume(level: int) -> bytes:
    return _u8(COMMANDS["SET_VOLUME"], _integer(level, 0, 9, "volume"))


def set_routine_volume(level: int) -> bytes:
    # The device uses 0..9 like SET_VOLUME (read back exactly on firmware
    # 0.3.7); the full byte stays accepted for lossless restore.
    return _u8(
        COMMANDS["SET_ROUTINE_MODE_VOLUME"],
        _integer(level, 0, 255, "routine volume byte"),
    )


# ---- system ----
def set_global_on(on: bool) -> bytes:
    """Start (True) or stop (False) the soother: light *and* sound.

    This is the deployed web client's "Start/Stop soother" control, not a
    light-only switch. Light on is ``set_light_color``, light off is
    ``turn_off_backlight``.
    """
    if type(on) is not bool:
        raise ValueError("soother state must be a boolean")
    return _u8(COMMANDS["SET_GLOBAL_ON"], 1 if on else 0)


def set_global_state(
    *,
    lights_on=None,
    brightness=None,
    music_on=None,
    volume=None,
    r2r=None,
    r2r_alarm=None,
    nap_alarm=None,
    routine=None,
) -> bytes:
    f = lambda x: NO_MODIFY if x is None else (int(x) & 0x0F)
    return bytes(
        [
            COMMANDS["SET_GLOBAL_STATE"],
            *reduce_low_nibbles(
                [
                    f(lights_on),
                    f(brightness),
                    f(music_on),
                    f(volume),
                    f(r2r),
                    f(r2r_alarm),
                    f(nap_alarm),
                    f(routine),
                ]
            ),
        ]
    )


def set_current_date(hour, minute, second, weekday) -> bytes:
    """Set the device clock; weekday counts Sunday as 0. No calendar date."""
    return _u8(
        COMMANDS["SET_CURRENT_DATE"],
        bcd(_integer(hour, 0, 23, "hour")),
        bcd(_integer(minute, 0, 59, "minute")),
        bcd(_integer(second, 0, 59, "second")),
        bcd(_integer(weekday, 0, 6, "weekday")),
    )


def set_clock_settings(display_on, brightness, fmt) -> bytes:
    return _u8(COMMANDS["SET_CLOCK_SETTINGS"]) + encode_clock_settings(
        ClockSettings(display_on, brightness, fmt)
    )


# ---- timers ----
def set_r2r_status(on: bool) -> bytes:
    if type(on) is not bool:
        raise ValueError("r2r status must be a boolean")
    return _u8(COMMANDS["SET_R2R_STATUS"], 1 if on else 0)


def start_nap(duration) -> bytes:
    # NapDuration enum 0..11 (0 = inactive), as validated by the web client.
    return _u8(COMMANDS["START_NAP_TIME"], _integer(duration, 0, 11, "nap duration"))


def set_nap_alarm(alarm) -> bytes:
    return _u8(COMMANDS["SET_NAP_TIME_ALARM"], _integer(alarm, 0, 10, "nap alarm"))


def set_r2r_times(week: WeeklyTimes) -> bytes:
    return _u8(COMMANDS["SET_R2R_TIMES"]) + encode_weekly_times(week)


def set_sleepy_times(week: WeeklyTimes) -> bytes:
    return _u8(COMMANDS["SET_SLEEPY_TIMES"]) + encode_weekly_times(week)


def set_r2r_alarms(alarms: WeeklyAlarms) -> bytes:
    return _u8(COMMANDS["SET_R2R_ALARMS"]) + encode_weekly_alarms(alarms)


# ---- routine ----
def set_routine_music_settings(settings: RoutineMusicSettings) -> bytes:
    """Set routine music and both reward sounds; not a routine start action.

    Each field is effectively a boolean on hardware (0 off, 1 on); the builder
    still accepts the full byte/nibble range, see ``RoutineMusicSettings``.
    """
    return _u8(COMMANDS["SET_ROUTINE_MUSIC_STATUS"]) + encode_routine_music_settings(
        settings
    )


def set_routine_status(on: bool) -> bytes:
    if type(on) is not bool:
        raise ValueError("routine status must be a boolean")
    return _u8(COMMANDS["SET_ROUTINE_MODE_STATUS"], 1 if on else 0)


def start_routine_mode() -> bytes:
    return _u8(COMMANDS["START_ROUTINE_MODE"])


def routine_control(ctrl) -> bytes:
    # RoutineControl enum 0..4.
    return _u8(
        COMMANDS["ROUTINE_CONTROL_COMMAND"], _integer(ctrl, 0, 4, "routine control")
    )


def _day_opcode(operation: str, day: Day) -> int:
    if not isinstance(day, str) or day not in DAY_ROUTINE[operation]:
        raise ValueError("unknown routine day")
    return DAY_ROUTINE[operation][day]


def set_day_routine(day: Day, routine: DailyRoutine) -> bytes:
    return _u8(_day_opcode("SET", day)) + encode_daily_routine(routine)


def request_day_routine(day: Day) -> bytes:
    return _u8(_day_opcode("REQUEST", day))


# ---- read-only queries ----
_REQUESTS = {
    "global_state": "REQUEST_GLOBAL_STATE",
    "current_date": "REQUEST_CURRENT_DATE",
    "toyic_fw_version": "REQUEST_TOYIC_FW_VERSION",
    "led_brightness": "REQUEST_LED_BRIGHTNESS",
    "light_color": "REQUEST_LIGHT_COLOR",
    "light_duration": "REQUEST_SOOTHER_MODE_LIGHT_DURATION",
    "volume": "REQUEST_VOLUME",
    "routine_volume": "REQUEST_ROUTINE_MODE_VOLUME",
    "song_playing": "REQUEST_SONG_PLAYING",
    "music_playlist": "REQUEST_MUSIC_PLAYLIST",
    "playlist_duration": "REQUEST_PLAYLIST_DURATION",
    "operation_mode": "REQUEST_OPERATION_MODE",
    "activity_state": "REQUEST_ACTIVITY_STATE",
    "current_stage": "REQUEST_CURRENT_STAGE",
    "clock_settings": "REQUEST_CLOCK_SETTINGS",
    "transmission_mode": "REQUEST_TRANSMISSION_MODE",
    "routine_mode_status": "REQUEST_ROUTINE_MODE_STATUS",
    "routine_music_status": "REQUEST_ROUTINE_MUSIC_STATUS",
    "r2r_status": "REQUEST_R2R_STATUS",
    "r2r_times": "REQUEST_R2R_TIMES",
    "sleepy_times": "REQUEST_SLEEPY_TIMES",
    "r2r_alarm_status": "REQUEST_R2R_ALARM_STATUS",
    "r2r_alarms": "REQUEST_R2R_ALARMS",
    "routine_task_status": "REQUEST_ROUTINE_TASK_STATUS",
    "nap_current_status": "REQUEST_CURRENT_NAP_TIME_STATUS",
    "nap_alarm_status": "REQUEST_NAP_TIME_ALARM_STATUS",
    "nap_alarm": "REQUEST_NAP_TIME_ALARM",
    "time_prescaler": "REQUEST_TIME_PRESCALER",
}


# Named requests that firmware 0.3.7 never answers (the request times out,
# which retires the session). The builders stay available for other firmware;
# do not include these in "read everything" loops.
UNANSWERED_REQUESTS: frozenset[str] = frozenset({"nap_alarm_status", "nap_alarm"})


def request(name: str) -> bytes:
    return _u8(COMMANDS[_REQUESTS[name]])


# transport-level: enable the toy-IC to stream responses (raw SSI0 ENABLE_RX)
ENABLE_RX = bytes([0x01, 0x50, 0x01])
