"""
lumalou — local BLE control for the Fisher-Price Lumalou (gld09).

Reverse-engineered MPID protocol: ECDH P-256 handshake + AES-128-CTR, pure Python.

    import asyncio
    from lumalou import LumalouClient, Color

    async def main():
        devices = await LumalouClient.scan()
        async with LumalouClient(devices[0]["address"]) as luma:
            await luma.light_color(Color.RAINBOW)
            print(await luma.request_state())

    asyncio.run(main())
"""

from ._generated import (
    Alarm,
    Audio,
    ClockFormat,
    Color,
    LightDuration,
    NapDuration,
    OperationMode,
    PlaylistDuration,
    RoutineControl,
    Song,
    Stage,
)
from .advertisement import (
    MANUFACTURER_ID,
    MANUFACTURER_PREFIX,
    LumalouAdvertisement,
    is_lumalou_advertisement,
    parse_advertisement,
)
from .client import (
    DisconnectedError,
    FactoryIdentityError,
    FactoryIdentityMismatchError,
    FreshSessionRequiredError,
    LumalouClient,
    LumalouError,
    MalformedResponseError,
    RequestTimeoutError,
    ResponseEnvelope,
    UnsupportedResponseError,
)
from .commands import UNANSWERED_REQUESTS
from .factory import (
    InvalidFactoryTokenError,
    parse_factory_device_fingerprint,
)
from .profile import ClockSettings, MusicPlaylist, OpaqueBlock, RoutineMusicSettings
from .protocol import build_tx_frame, crc8, decrypt_rx_frame, encode_command
from .responses import (
    SINGLE_VALUE_RESPONSES,
    CurrentDate,
    parse_clock_settings,
    parse_current_date,
    parse_music_playlist,
    parse_routine_music_status,
    parse_single_value,
)
from .schedules import RoutineTaskState, RoutineTaskStatus

__version__ = "0.2.1"

__all__ = [
    "MANUFACTURER_ID",
    "MANUFACTURER_PREFIX",
    "SINGLE_VALUE_RESPONSES",
    "UNANSWERED_REQUESTS",
    "Alarm",
    "Audio",
    "ClockFormat",
    "ClockSettings",
    "Color",
    "CurrentDate",
    "DisconnectedError",
    "FactoryIdentityError",
    "FactoryIdentityMismatchError",
    "FreshSessionRequiredError",
    "InvalidFactoryTokenError",
    "LightDuration",
    "LumalouAdvertisement",
    "LumalouClient",
    "LumalouError",
    "MalformedResponseError",
    "MusicPlaylist",
    "NapDuration",
    "OpaqueBlock",
    "OperationMode",
    "PlaylistDuration",
    "RequestTimeoutError",
    "ResponseEnvelope",
    "RoutineControl",
    "RoutineMusicSettings",
    "RoutineTaskState",
    "RoutineTaskStatus",
    "Song",
    "Stage",
    "UnsupportedResponseError",
    "build_tx_frame",
    "crc8",
    "decrypt_rx_frame",
    "encode_command",
    "is_lumalou_advertisement",
    "parse_advertisement",
    "parse_clock_settings",
    "parse_current_date",
    "parse_factory_device_fingerprint",
    "parse_music_playlist",
    "parse_routine_music_status",
    "parse_single_value",
]
