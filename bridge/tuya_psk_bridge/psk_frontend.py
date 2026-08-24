"""TLS-PSK key derivation for Tuya local cloud devices.

Refactored from the lab PSK frontend proxy.  The Tuya device presents a PSK
identity string during the TLS-PSK handshake; this module derives the shared
pre-shared key from that identity and the server's PSK hint.

Security notes
--------------
- The identity and hint are **logged as hex only** for debugging connectivity
  issues.  The derived PSK and raw secret material are **never** logged.
- The derivation algorithm is an MD5-based AES-CBC construction observed in
  Tuya's firmware; it is not a standard KDF.  We faithfully reproduce it so the
  bridge can terminate TLS-PSK connections from existing devices.
"""

from __future__ import annotations

import logging
from binascii import hexlify
from hashlib import md5

from Cryptodome.Cipher import AES

logger = logging.getLogger(__name__)

# First byte of the identity is a length prefix; the actual identity data
# follows.  Tuya devices consistently send at least 33 bytes after this prefix
# so that ``identity[1:33]`` is available for AES encryption.
_IDENTITY_LENGTH_PREFIX_LEN = 1

# AES block size in bytes (AES-128-CBC).
_BLOCK_SIZE = 16

# The PSK derivation encrypts exactly 2 AES blocks (32 bytes) of the identity.
# If the stripped identity is shorter than 32 bytes but >= 16 bytes, we pad
# with zero bytes to reach 32.  This matches the Tuya firmware behaviour where
# identities are always >= 32 bytes, but provides graceful handling for edge
# cases observed in testing.
_AES_INPUT_LEN = 32

# The PSK hint observed from the Tuya cloud endpoint. Stable across the
# captured firmware. Configurable in a future version if needed.
DEFAULT_HINT = b"1dHRsc2NjbHltbGx3eWh50000000000000000"

# Fixed prefix carried by scheme-1 identities, immediately after the version
# byte. Upstream tuya-convert validates this exact string before deriving, and
# this derivation is only proven for identities that carry it. Devices using a
# later scheme (version byte 0x03 has been observed) have a different identity
# layout and a PSK this function cannot produce.
_SCHEME_1_PREFIX = b"BAohbmd6aG91IFR1"


def derive_psk(identity: bytes, hint: bytes) -> bytes:
    """Derive the 32-byte TLS-PSK from the Tuya identity and hint.

    The derivation mirrors Tuya's firmware logic:

    1. Strip the leading length-prefix byte from *identity*.
    2. Compute the AES key as ``MD5(hint[-16:])``.
    3. Compute the AES IV as ``MD5(stripped_identity)``.
    4. Encrypt the first 32 bytes of the stripped identity with AES-128-CBC
       using the key and IV from steps 2-3.
    5. Return the resulting 32-byte ciphertext (the PSK).

    Args:
        identity: Raw TLS-PSK identity bytes as received from the device
            (includes the leading length-prefix byte).
        hint: PSK hint bytes configured on the server side.

    Returns:
        A 32-byte ``bytes`` object suitable for use as the TLS-PSK.

    Raises:
        ValueError: If the identity is too short after stripping the prefix
            (must be at least ``_BLOCK_SIZE`` bytes for AES CBC to produce a
            full block).
    """
    # Log identity as hex for connectivity debugging — never the hint or PSK.
    logger.debug("PSK identity hex: %s", hexlify(identity).decode("ascii"))

    stripped = identity[_IDENTITY_LENGTH_PREFIX_LEN:]

    if not stripped.startswith(_SCHEME_1_PREFIX):
        # Upstream tuya-convert rejects the identity outright here. We continue
        # so the handshake failure stays in one place, but say plainly what is
        # wrong: without this the only symptom is an opaque "record layer
        # failure" from OpenSSL, which cost a full evening to attribute.
        logger.error(
            "PSK identity version byte 0x%02x does not carry the known scheme-1 "
            "prefix; this derivation is only proven for scheme 1 and the "
            "handshake is expected to fail. See docs/device-giex-water-timer.md",
            identity[0] if identity else 0,
        )

    if len(stripped) < _BLOCK_SIZE:
        raise ValueError(
            f"Identity too short after prefix removal: "
            f"have {len(stripped)} bytes, need at least {_BLOCK_SIZE}"
        )

    # Take up to 32 bytes of the stripped identity; pad with zeros if shorter.
    aes_input = stripped[:_AES_INPUT_LEN]
    if len(aes_input) < _AES_INPUT_LEN:
        aes_input = aes_input + b"\x00" * (_AES_INPUT_LEN - len(aes_input))

    key = md5(hint[-16:]).digest()
    iv = md5(stripped).digest()
    cipher = AES.new(key, AES.MODE_CBC, iv)
    psk = cipher.encrypt(aes_input)

    logger.debug("Derived PSK (%d bytes) for identity (stripped len=%d)", len(psk), len(stripped))
    return psk


# ---------------------------------------------------------------------------
# Lab variants
# ---------------------------------------------------------------------------
#
# :func:`derive_psk` is the derivation proven against the door sensors on 8886
# and must not be changed speculatively. The GIEX water timer on 8883 presents a
# 49-byte identity shaped as ``0x03`` + 16 per-boot bytes + 32 bytes that are
# constant across boots, and rejects the PSK :func:`derive_psk` produces.
#
# These variants slice that identity differently so a candidate can be tried on
# one listener without touching the port the door sensors use. They are lab
# tools: when one is confirmed it should replace this table with a documented
# derivation, not linger as a switch.


def _variant_tail32(identity: bytes, hint: bytes) -> bytes:
    """Encrypt the trailing 32 bytes — the part constant across boots."""
    stripped = identity[_IDENTITY_LENGTH_PREFIX_LEN:]
    key = md5(hint[-16:]).digest()
    iv = md5(stripped).digest()
    return AES.new(key, AES.MODE_CBC, iv).encrypt(stripped[-_AES_INPUT_LEN:])


def _variant_head16_iv(identity: bytes, hint: bytes) -> bytes:
    """Same input as the default, but the IV covers only the per-boot bytes."""
    stripped = identity[_IDENTITY_LENGTH_PREFIX_LEN:]
    key = md5(hint[-16:]).digest()
    iv = md5(stripped[:_BLOCK_SIZE]).digest()
    return AES.new(key, AES.MODE_CBC, iv).encrypt(stripped[:_AES_INPUT_LEN])


def _variant_full(identity: bytes, hint: bytes) -> bytes:
    """Encrypt the whole stripped identity rather than a fixed 32 bytes."""
    stripped = identity[_IDENTITY_LENGTH_PREFIX_LEN:]
    usable = len(stripped) - (len(stripped) % _BLOCK_SIZE)
    key = md5(hint[-16:]).digest()
    iv = md5(stripped).digest()
    return AES.new(key, AES.MODE_CBC, iv).encrypt(stripped[:usable])


def _variant_full_hint_key(identity: bytes, hint: bytes) -> bytes:
    """Key from the whole hint instead of its last 16 bytes."""
    stripped = identity[_IDENTITY_LENGTH_PREFIX_LEN:]
    key = md5(hint).digest()
    iv = md5(stripped).digest()
    return AES.new(key, AES.MODE_CBC, iv).encrypt(stripped[:_AES_INPUT_LEN])


def _variant_no_prefix_strip(identity: bytes, hint: bytes) -> bytes:
    """Treat the leading byte as identity data rather than a length prefix."""
    key = md5(hint[-16:]).digest()
    iv = md5(identity).digest()
    return AES.new(key, AES.MODE_CBC, iv).encrypt(identity[:_AES_INPUT_LEN])


PSK_VARIANTS = {
    "default": derive_psk,
    "tail32": _variant_tail32,
    "head16_iv": _variant_head16_iv,
    "full": _variant_full,
    "full_hint_key": _variant_full_hint_key,
    "no_prefix_strip": _variant_no_prefix_strip,
}


def get_psk_deriver(variant: str | None):
    """Look up a derivation by name; ``None`` or ``"default"`` is the proven one.

    Raises:
        ValueError: If the name is not a known variant.
    """
    if not variant:
        return derive_psk
    try:
        return PSK_VARIANTS[variant]
    except KeyError:
        raise ValueError(
            f"Unknown PSK variant '{variant}'; known: {sorted(PSK_VARIANTS)}"
        ) from None
