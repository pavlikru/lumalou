"""Decode device responses. Values are raw integers (canonical); use label() for names."""

from __future__ import annotations

from ._generated import RESPONSES, Color, OperationMode, Stage
from .schedules import (
    DAY_ROUTINE_RESPONSES,
    DailyRoutine,
    RoutineTaskStatus,
    WeeklyAlarms,
    WeeklyTimes,
    decode_daily_routine,
    decode_routine_task_status,
    decode_weekly_alarms,
    decode_weekly_times,
)


def parse_r2r_times(args: bytes) -> WeeklyTimes:
    return decode_weekly_times(args)


def parse_sleepy_times(args: bytes) -> WeeklyTimes:
    return decode_weekly_times(args)


def parse_r2r_alarms(args: bytes) -> WeeklyAlarms:
    return decode_weekly_alarms(args)


def parse_day_routine(args: bytes) -> DailyRoutine:
    return decode_daily_routine(args)


def parse_routine_task_status(args: bytes) -> RoutineTaskStatus:
    return decode_routine_task_status(args)


def parse_schedule_response(
    opcode: int, args: bytes
) -> WeeklyTimes | WeeklyAlarms | DailyRoutine | RoutineTaskStatus:
    """Decode a known schedule/status response; never infer an unknown layout."""
    if type(opcode) is not int:
        raise ValueError("response opcode must be an integer")
    if opcode in (0x22, 0x23):
        return decode_weekly_times(args)
    if opcode == 0x27:
        return decode_weekly_alarms(args)
    if opcode in DAY_ROUTINE_RESPONSES.values():
        return decode_daily_routine(args)
    if opcode == 0x94:
        return decode_routine_task_status(args)
    raise ValueError(f"unsupported schedule response: {opcode:#x}")


def _nibbles(data: bytes):
    out = []
    for b in data:
        out.append(b >> 4)
        out.append(b & 0x0F)
    return out


def parse_global_state(args: bytes) -> dict:
    """Decode the GLOBAL_STATE snapshot (response 0x02) into raw integer fields."""
    if not isinstance(args, bytes) or len(args) != 13:
        raise ValueError("GLOBAL_STATE must contain exactly 13 bytes")
    n = _nibbles(args)
    return {
        "operationMode": n[0],
        "activityState": n[1],
        "musicStatus": n[2],
        "currentSong": (n[3] << 4) | n[4],
        "currentVolume": n[5],
        "playlistDuration": n[6],
        "lightStatus": n[7],
        "lightBrightness": n[8],
        "lightColor": n[9],
        "napTimeStatus": n[10],
        "napDuration": n[11],
        "ready2RiseStatus": n[12],
        "ready2RiseAlarmStatus": n[13],
        "timePrescaler": n[14],
        "currentStage": n[15],
        "clockDisplay": n[16],
        "clockBrightness": n[17],
        "clockFormat": n[18],
        "routineMusicStatus": n[19],
        "taskRewardSfx": n[20],
        "routineRewardSfx": n[21],
        "lightDuration": n[22],
        "routineVolume": n[23],
        "routineModeStatus": n[24],
        "alarmExecuting": n[25],
    }


def response_name(opcode: int) -> str:
    return RESPONSES.get(opcode, f"0x{opcode:02x}")


def label(state: dict) -> dict:
    """Add human-readable labels to a decoded GLOBAL_STATE (non-destructive)."""
    out = dict(state)
    try:
        out["operationModeLabel"] = OperationMode(state["operationMode"]).name
    except ValueError:
        pass
    try:
        out["currentStageLabel"] = Stage(state["currentStage"]).name
    except ValueError:
        pass
    try:
        out["lightColorLabel"] = Color(state["lightColor"]).name
    except ValueError:
        pass
    return out
