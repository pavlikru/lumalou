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
from .client import (
    DisconnectedError,
    FreshSessionRequiredError,
    LumalouClient,
    LumalouError,
    MalformedResponseError,
    RequestTimeoutError,
    ResponseEnvelope,
    UnsupportedResponseError,
)
from .profile import ClockSettings, MusicPlaylist, OpaqueBlock, RoutineMusicSettings
from .protocol import build_tx_frame, crc8, decrypt_rx_frame, encode_command

__version__ = "0.1.0"

__all__ = [
    "Alarm",
    "Audio",
    "ClockFormat",
    "ClockSettings",
    "Color",
    "DisconnectedError",
    "FreshSessionRequiredError",
    "LightDuration",
    "LumalouClient",
    "LumalouError",
    "MalformedResponseError",
    "MusicPlaylist",
    "NapDuration",
    "OperationMode",
    "OpaqueBlock",
    "PlaylistDuration",
    "RequestTimeoutError",
    "ResponseEnvelope",
    "RoutineControl",
    "RoutineMusicSettings",
    "Song",
    "Stage",
    "UnsupportedResponseError",
    "build_tx_frame",
    "crc8",
    "decrypt_rx_frame",
    "encode_command",
]
