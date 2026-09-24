"""Pure signed-field verification and item-code decoding for a FACTORY token.

The 192-byte layout and manufacturing public key are documented by the
independent MIT-licensed gld09-control project at commit
6e3aff894b0065b760ba44f43a36c7e9988cead9. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

FACTORY_TOKEN_LENGTH = 192
_SIGNED_END = 124
_SIGNATURE_END = 188
_SERIAL_START = 1
_SERIAL_END = 25
_ITEM_START = 19
_ITEM_END = 25
_DEVICE_KEY_START = 25
_DEVICE_KEY_END = 58
_KEY_ID_START = 65
_KEY_ID_END = 68
_TOKEN_VERSION = 10

# Public verification key only; the manufacturing private key is not present.
MANUFACTURING_PUBLIC_KEYS: Mapping[int, bytes] = MappingProxyType(
    {
        12: bytes.fromhex(
            "f3d2307c7faafc2884fb306f7ecb6281ecc4871862dfe66d578cf8b6e36f543d"
            "458b6dfd9b8a8be8346fd9f4d3e3ec8836e1135e8d474bd28f151c94b04c0a56"
        )
    }
)


class InvalidFactoryTokenError(ValueError):
    """The FACTORY token cannot be authenticated or safely decoded."""


def parse_factory_item_code(
    token: bytes,
    *,
    keys: Mapping[int, bytes] | None = None,
    supported_codes: AbstractSet[str] | None = None,
) -> str:
    """Verify FACTORY signed bytes and return the exact six-character item field.

    The returned code is lowercase ASCII, with all six characters retained.
    No padding convention or GLD09 mapping is assumed. ``supported_codes``
    optionally enforces exact lowercase six-character matches. ``keys`` may
    replace the trusted public-key table for a different verified source or
    synthetic tests; callers must authenticate that source themselves.

    The four-byte salt after the signature is outside the signed region. This
    function authenticates only the signed fields and does not use the salt.

    Neither token nor serial is included in errors or returned data.
    """
    if not isinstance(token, bytes):
        raise TypeError("factory token must be bytes")
    if supported_codes is not None and (
        isinstance(supported_codes, (str, bytes))
        or any(
            not isinstance(code, str)
            or len(code) != _ITEM_END - _ITEM_START
            or not code.isascii()
            or code != code.lower()
            or any(not 0x20 <= ord(character) <= 0x7E for character in code)
            for code in supported_codes
        )
    ):
        raise ValueError("supported item codes must be six-character lowercase ASCII")
    if len(token) != FACTORY_TOKEN_LENGTH:
        raise InvalidFactoryTokenError("factory token has invalid length")
    if token[0] != _TOKEN_VERSION:
        raise InvalidFactoryTokenError("unsupported factory token version")

    key_id = int.from_bytes(token[_KEY_ID_START:_KEY_ID_END], "big")
    trusted_keys = MANUFACTURING_PUBLIC_KEYS if keys is None else keys
    public_xy = trusted_keys.get(key_id)
    if not isinstance(public_xy, bytes) or len(public_xy) != 64:
        raise InvalidFactoryTokenError("unknown or invalid manufacturing key")
    try:
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), b"\x04" + public_xy
        )
    except ValueError as error:
        raise InvalidFactoryTokenError("invalid manufacturing key") from error

    raw_signature = token[_SIGNED_END:_SIGNATURE_END]
    signature = encode_dss_signature(
        int.from_bytes(raw_signature[:32], "big"),
        int.from_bytes(raw_signature[32:], "big"),
    )
    try:
        public_key.verify(signature, token[:_SIGNED_END], ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as error:
        raise InvalidFactoryTokenError("factory token signature is invalid") from error

    try:
        ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), token[_DEVICE_KEY_START:_DEVICE_KEY_END]
        )
    except ValueError as error:
        raise InvalidFactoryTokenError("factory token device key is invalid") from error

    serial_bytes = token[_SERIAL_START:_SERIAL_END]
    if any(byte < 0x20 or byte > 0x7E for byte in serial_bytes):
        raise InvalidFactoryTokenError("factory token serial is not printable ASCII")
    item_code = token[_ITEM_START:_ITEM_END].decode("ascii").lower()
    if supported_codes is not None and item_code not in supported_codes:
        raise InvalidFactoryTokenError("unsupported factory item code")
    return item_code
