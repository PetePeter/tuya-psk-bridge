"""Tests for TLS-PSK derivation in psk_frontend.derive_psk.

All fixtures are synthetic — no real device secrets or captured traffic.
"""

from __future__ import annotations

from hashlib import md5

import pytest

from tuya_psk_bridge.psk_frontend import derive_psk, _AES_INPUT_LEN


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_identity(suffix: bytes = b"") -> bytes:
    """Build a synthetic PSK identity with a leading length-prefix byte.

    The body is always exactly 32 bytes (padded with zeros) so the AES-CBC
    encryption in derive_psk receives a full 2-block input without needing
    internal padding.
    """
    base = b"ABCDEFGHIJKLMNOP"  # 16 bytes
    raw = base + suffix
    # Pad or truncate to exactly 32 bytes.
    if len(raw) < 32:
        raw = raw + b"\x00" * (32 - len(raw))
    else:
        raw = raw[:32]
    return bytes([len(raw)]) + raw


_HINT = b"0123456789abcdef" + b"0123456789abcdef"  # 32 bytes
_SHORT_HINT = b"short"  # less than 16 bytes — tests hint[-16:] truncation


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------

class TestDerivePskConsistency:
    """Same inputs must always produce the same PSK."""

    def test_idempotent(self):
        identity = _make_identity(b"test1")
        psk1 = derive_psk(identity, _HINT)
        psk2 = derive_psk(identity, _HINT)
        assert psk1 == psk2

    def test_returns_32_bytes(self):
        psk = derive_psk(_make_identity(), _HINT)
        assert len(psk) == 32


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------

class TestDerivePskUniqueness:
    """Different identities should produce different PSKs (with high probability)."""

    def test_different_identities(self):
        id_a = _make_identity(b"alpha")
        id_b = _make_identity(b"beta")
        psk_a = derive_psk(id_a, _HINT)
        psk_b = derive_psk(id_b, _HINT)
        assert psk_a != psk_b

    def test_identity_differs_only_in_suffix(self):
        """Even a 1-byte difference in the identity body changes the PSK."""
        id_a = _make_identity(b"X")
        id_b = _make_identity(b"Y")
        psk_a = derive_psk(id_a, _HINT)
        psk_b = derive_psk(id_b, _HINT)
        assert psk_a != psk_b


# ---------------------------------------------------------------------------
# Hint dependency
# ---------------------------------------------------------------------------

class TestDerivePskHint:
    """The PSK must change when the hint changes (because MD5(hint[-16:]) is the key)."""

    def test_different_hints(self):
        identity = _make_identity(b"same")
        hint_a = b"A" * 32
        hint_b = b"B" * 32
        psk_a = derive_psk(identity, hint_a)
        psk_b = derive_psk(identity, hint_b)
        assert psk_a != psk_b

    def test_hint_shorter_than_16_bytes(self):
        """When hint is shorter than 16 bytes, hint[-16:] is the entire hint."""
        identity = _make_identity(b"short_hint")
        # This should not raise — MD5 of the short hint is still valid.
        psk = derive_psk(identity, _SHORT_HINT)
        assert len(psk) == 32

    def test_hint_exactly_16_bytes(self):
        """hint[-16:] with exactly 16 bytes is just the hint itself."""
        identity = _make_identity(b"exact16")
        hint = b"0123456789abcdef"
        psk = derive_psk(identity, hint)
        assert len(psk) == 32


# ---------------------------------------------------------------------------
# Identity prefix pattern
# ---------------------------------------------------------------------------

class TestDerivePskLabPrefix:
    """The lab PSK frontend checks for a known identity prefix.

    In production we do NOT enforce this (devices may vary), but we verify
    that identities with and without the prefix both produce valid PSKs.
    """

    # The real prefix observed in the lab (base64 of part of the identity).
    _LAB_PREFIX = b"BAohbmd6aG91IFR1"

    def _make_lab_identity(self) -> bytes:
        """Build a 32-byte body starting with the lab prefix, zero-padded."""
        body = self._LAB_PREFIX
        if len(body) < 32:
            body = body + b"\x00" * (32 - len(body))
        else:
            body = body[:32]
        return bytes([len(body)]) + body

    def test_with_lab_prefix(self):
        """An identity carrying the known lab prefix should still derive."""
        psk = derive_psk(self._make_lab_identity(), _HINT)
        assert len(psk) == 32

    def test_without_lab_prefix(self):
        """An identity without the lab prefix should also derive fine."""
        psk = derive_psk(_make_identity(b"other_data"), _HINT)
        assert len(psk) == 32

    def test_lab_prefix_and_random_produce_different_psk(self):
        psk_lab = derive_psk(self._make_lab_identity(), _HINT)
        psk_other = derive_psk(_make_identity(b"other"), _HINT)
        assert psk_lab != psk_other


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestDerivePskEdgeCases:
    def test_identity_too_short_raises(self):
        """Identity with fewer than 16 bytes after the prefix byte must fail."""
        identity = bytes([8]) + b"12345678"
        with pytest.raises(ValueError, match="too short"):
            derive_psk(identity, _HINT)

    def test_identity_exactly_16_bytes_after_prefix(self):
        """Borderline: exactly 16 bytes after prefix — padded to 32 internally."""
        body = b"A" * 16
        identity = bytes([len(body)]) + body
        psk = derive_psk(identity, _HINT)
        assert len(psk) == 32

    def test_identity_17_bytes_after_prefix(self):
        """17 bytes after prefix — padded to 32 with one zero byte."""
        body = b"A" * 17
        identity = bytes([len(body)]) + body
        psk = derive_psk(identity, _HINT)
        assert len(psk) == 32

    def test_empty_identity_raises(self):
        """Identity that is only the prefix byte with no data."""
        identity = bytes([0])
        with pytest.raises(ValueError, match="too short"):
            derive_psk(identity, _HINT)

    def test_single_byte_identity_raises(self):
        identity = bytes([1]) + b"X"
        with pytest.raises(ValueError, match="too short"):
            derive_psk(identity, _HINT)

    def test_long_identity_works(self):
        """Identities much longer than 32 bytes — only first 32 used."""
        body = b"X" * 128
        identity = bytes([len(body)]) + body
        psk = derive_psk(identity, _HINT)
        assert len(psk) == 32

    def test_exactly_32_bytes_after_prefix(self):
        """Exactly 32 bytes after prefix — no internal padding needed."""
        body = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"  # 32 bytes
        identity = bytes([len(body)]) + body
        psk = derive_psk(identity, _HINT)
        assert len(psk) == 32


# ---------------------------------------------------------------------------
# Manual verification of derivation algorithm
# ---------------------------------------------------------------------------

class TestDerivePskAlgorithm:
    """Verify the derivation against a manual computation of the same steps."""

    def test_matches_manual_computation(self):
        from Cryptodome.Cipher import AES

        identity_body = b"ABCDEFGHIJKLMNOP" + b"test_suffix_pad!!"  # 32 bytes
        identity = bytes([len(identity_body)]) + identity_body
        hint = b"0123456789abcdef" + b"FEDCBA9876543210"

        # Manual derivation matching derive_psk internals exactly.
        stripped = identity[1:]  # 32 bytes
        aes_input = stripped[:32]
        # No padding needed — exactly 32 bytes.
        key = md5(hint[-16:]).digest()
        iv = md5(stripped).digest()
        cipher = AES.new(key, AES.MODE_CBC, iv)
        expected_psk = cipher.encrypt(aes_input)

        actual_psk = derive_psk(identity, hint)
        assert actual_psk == expected_psk

    def test_manual_computation_with_padding(self):
        """When identity body is 17 bytes, verify zero-padding matches."""
        from Cryptodome.Cipher import AES

        identity_body = b"A" * 17  # 17 bytes — needs padding to 32
        identity = bytes([len(identity_body)]) + identity_body
        hint = _HINT

        stripped = identity[1:]
        aes_input = stripped[:32] + b"\x00" * (32 - len(stripped[:32]))
        key = md5(hint[-16:]).digest()
        iv = md5(stripped).digest()
        cipher = AES.new(key, AES.MODE_CBC, iv)
        expected_psk = cipher.encrypt(aes_input)

        actual_psk = derive_psk(identity, hint)
        assert actual_psk == expected_psk
