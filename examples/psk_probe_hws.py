#!/usr/bin/env python3
"""psk_probe — brute-force the scheme-02 TLS-PSK derivation for the Rinnai HWS.

Background: door sensors (scheme 01) accept PSK = AES-128-CBC(
    key=MD5(hint[-16:]), iv=MD5(identity[1:])).encrypt(identity[1:33])
derived purely from wire data — no per-device secret.  The HWS presents a
scheme-02 identity (0x02 | rand16 | const32) and rejects that formula.

The unit retries :8883 every 20-45 s while WAN-blocked, so with a DNAT in
place each TCP retry probes one candidate derivation.  The winner is the
candidate in force when the device completes the handshake and sends its
MQTT CONNECT.

State (attempt index / winner) persists to STATE_FILE so restarts resume.
Scheme-01 identities fall through to the proven formula so an accidental
door-sensor connection still works.

Usage:
    python3 psk_probe_hws.py            # serve on :8886
    python3 psk_probe_hws.py --selftest # verify all candidates build
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import ssl
import sys
import threading
from binascii import hexlify
from hashlib import md5

try:
    import sslpsk  # only needed for serve(); selftest runs without it
except ImportError:  # pragma: no cover
    sslpsk = None
from Crypto.Cipher import AES  # pycryptodome

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("psk_probe")

LISTEN = ("0.0.0.0", 8886)
RST_NEXT = 0  # when >0, hard-reset the next N accepts to probe retry cadence
HINT = b"1dHRsc2NjbHltbGx3eWh50000000000000000"
STATE_FILE = "/tmp/psk_probe_state.json"

_state_lock = threading.Lock()
_state: dict = {"attempt": 0, "winner": None}


def _load_state() -> None:
    global _state
    try:
        with open(STATE_FILE) as f:
            _state.update(json.load(f))
        log.info("state loaded: attempt=%s winner=%s", _state["attempt"], _state["winner"])
    except (OSError, ValueError):
        pass


def _save_state() -> None:
    with _state_lock:
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f)


# ---------------------------------------------------------------------------
# Candidate derivations
# ---------------------------------------------------------------------------


def _slices(I: bytes) -> dict[str, bytes]:
    """Named input-slice candidates over a 49-byte scheme-02 identity."""
    pad = lambda b: b + b"\x00" * ((-len(b)) % 32)
    return {
        "head+tail16": I[1:33],       # proven formula's slice
        "tail": I[17:49],
        "full32": I[:32],
        "all48": I[1:49],
        "skip2": I[2:34],
        "head_pad": pad(I[1:17]),
    }


def _keys(H: bytes) -> dict[str, bytes]:
    return {
        "md5(hint-16)": md5(H[-16:]).digest(),
        "md5(hint)": md5(H).digest(),
        "hint-16": H[-16:],
        "md5(hint+16)": md5(H[:16]).digest(),
    }


def _ivs(I: bytes, P: bytes, T: bytes) -> dict[str, bytes]:
    return {
        "md5(id1:)": md5(I[1:]).digest(),
        "md5(P)": md5(P).digest(),
        "md5(T)": md5(T).digest(),
        "md5(I)": md5(I).digest(),
        "zero": b"\x00" * 16,
    }


def build_candidates() -> list[tuple[str, object]]:
    """Ordered (name, fn(I, hint) -> 32 bytes). Proven components first."""
    out: list[tuple[str, object]] = []

    def reg(name, fn):
        out.append((name, fn))

    def make(P_name, K_name, mode, IV_name=None, take="first"):
        def fn(I: bytes, H: bytes) -> bytes:
            P = _slices(I)[P_name]
            P = P + b"\x00" * ((-len(P)) % 16)
            K = _keys(H)[K_name]
            if mode == "ecb":
                ct = AES.new(K, AES.MODE_ECB).encrypt(P)
            else:
                IV = _ivs(I, P, I[17:49])[IV_name]
                ct = AES.new(K, AES.MODE_CBC, IV).encrypt(P)
            return ct[-32:] if take == "last" and len(ct) > 32 else ct[:32]

        return fn

    slice_names = ["head+tail16", "tail", "full32", "all48", "skip2", "head_pad"]
    key_names = list(_keys(HINT))
    iv_names = ["md5(id1:)", "md5(P)", "md5(T)", "md5(I)", "zero"]

    # Wave 1: proven key + proven IV + CBC, every slice.
    for s in slice_names:
        reg(f"cbc/k1v1/{s}", make(s, key_names[0], "cbc", iv_names[0]))
    # Wave 1b: 48-byte slice, take-last variant.
    reg("cbc/k1v1/all48-last", make("all48", key_names[0], "cbc", iv_names[0], take="last"))
    # Wave 2: proven key, other IVs.
    for s in slice_names:
        for v in iv_names[1:]:
            reg(f"cbc/k1{v}/{s}", make(s, key_names[0], "cbc", v))
    # Wave 3: other keys (proven IV, CBC; then ECB).
    for k in key_names[1:]:
        for s in ("tail", "head+tail16", "all48"):
            reg(f"cbc/{k}v1/{s}", make(s, k, "cbc", iv_names[0]))
        reg(f"ecb/{k}/tail", make("tail", k, "ecb"))

    # Wave 5 (round 2, added after round 1 exhausted): sha256/hmac family.
    from hashlib import sha256

    def make5(P_name, kname, kfn, ivname, ivfn):
        def fn(I: bytes, H: bytes) -> bytes:
            P = _slices(I)[P_name]
            P = P + b"\x00" * ((-len(P)) % 16)
            ct = AES.new(kfn(H), AES.MODE_CBC, ivfn(I, P)).encrypt(P)
            return ct[:32]

        return fn

    sha_keys = {
        "sha256(hint)16": lambda H: sha256(H).digest()[:16],
        "md5(sha256(hint))": lambda H: md5(sha256(H).digest()).digest(),
    }
    sha_ivs = {
        "sha256(id1:)16": lambda I, P: sha256(I[1:]).digest()[:16],
        "md5(sha256(P))": lambda I, P: md5(sha256(P).digest()).digest(),
        "zero": lambda I, P: b"\x00" * 16,
    }
    for kn, kf in sha_keys.items():
        for vn, vf in sha_ivs.items():
            for s in ("tail", "head+tail16"):
                reg(f"r2/cbc/{kn}{vn}/{s}", make5(s, kn, kf, vn, vf))
    # Direct-digest PSKs (no AES at all).
    reg("r2/sha256(T)", lambda I, H: sha256(I[17:49]).digest())
    reg("r2/sha256(id1:)", lambda I, H: sha256(I[1:]).digest())
    reg("r2/hmac(hint,T)", lambda I, H: __import__("hmac").new(H, I[17:49], sha256).digest())
    return out


CANDIDATES = build_candidates()


def derive_psk_01(identity: bytes, hint: bytes) -> bytes:
    """Proven scheme-01 derivation (door sensors)."""
    stripped = identity[1:]
    aes_input = (stripped[:32] + b"\x00" * 32)[:32]
    key = md5(hint[-16:]).digest()
    iv = md5(stripped).digest()
    return AES.new(key, AES.MODE_CBC, iv).encrypt(aes_input)


def psk_callback(identity: bytes) -> bytes:
    ihex = hexlify(identity).decode()
    if not identity or identity[0] != 0x02:
        log.info("non-02 identity, proven path: %s", ihex)
        return derive_psk_01(identity, HINT)

    with _state_lock:
        if _state["winner"]:
            name = _state["winner"]
            idx = next(i for i, (n, _) in enumerate(CANDIDATES) if n == name)
            _state["attempt"] += 1
        else:
            idx = _state["attempt"] % len(CANDIDATES)
            _state["attempt"] += 1
    name, fn = CANDIDATES[idx]
    log.info("ATTEMPT %d/%d variant=%s identity=%s",
             _state["attempt"], len(CANDIDATES), name, ihex)
    _save_state()
    return fn(identity, HINT)


def mark_winner() -> str | None:
    """Called when a scheme-02 session reaches MQTT CONNECT: freeze the variant."""
    with _state_lock:
        idx = (_state["attempt"] - 1) % len(CANDIDATES)
        name = CANDIDATES[idx][0]
        if _state["winner"] != name:
            _state["winner"] = name
            _save_state()
            log.info("*** WINNER variant=%s ***", name)
        return name


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def serve() -> None:
    _load_state()
    log.info("%d candidates loaded; listening on %s:%d", len(CANDIDATES), *LISTEN)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(8)

    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle, args=(client, addr), daemon=True).start()


def handle(client: socket.socket, addr) -> None:
    global RST_NEXT
    if RST_NEXT > 0:
        RST_NEXT -= 1
        log.info("RST-PROBE from %s (rst_remaining=%d)", addr[0], RST_NEXT)
        import struct as _struct

        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _struct.pack("ii", 1, 0))
        client.close()
        return
    try:
        tls = sslpsk.wrap_socket(
            client,
            server_side=True,
            ssl_version=ssl.PROTOCOL_TLSv1_2,
            ciphers="PSK-AES128-CBC-SHA256",
            psk=psk_callback,
            hint=HINT,
        )
        tls.settimeout(30)
        data = tls.recv(4096)
        if data:
            log.info("PAYLOAD from %s (%d bytes): %s", addr[0], len(data), hexlify(data[:120]).decode())
            if data[0] & 0xF0 == 0x10:  # MQTT CONNECT
                log.info("MQTT CONNECT received — handshake validated by device")
                mark_winner()
                tls.sendall(bytes([0x20, 0x02, 0x00, 0x00]))  # CONNACK
                while True:
                    more = tls.recv(4096)
                    if not more:
                        break
                    log.info("PAYLOAD (%d bytes): %s", len(more), hexlify(more[:200]).decode())
        else:
            log.info("handshake returned, device sent nothing (wrong PSK?)")
    except ssl.SSLError as exc:
        log.info("TLS failure from %s: %s", addr[0], exc)
    except OSError as exc:
        log.info("socket error from %s: %s", addr[0], exc)
    finally:
        try:
            client.close()
        except OSError:
            pass


def selftest() -> int:
    synthetic = b"\x02" + bytes(range(16)) + bytes(range(32))
    bad = 0
    for name, fn in CANDIDATES:
        try:
            psk = fn(synthetic, HINT)
            assert len(psk) == 32, f"len={len(psk)}"
        except Exception as exc:  # noqa: BLE001
            log.error("SELFTEST FAIL %s: %s", name, exc)
            bad += 1
    p01 = derive_psk_01(b"\x01" + b"A" * 49, HINT)
    assert len(p01) == 32
    log.info("selftest: %d candidates OK, %d failed", len(CANDIDATES), bad)
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--rst", type=int, default=0, help="hard-RST the next N connections")
    args = ap.parse_args()
    RST_NEXT = args.rst
    sys.exit(selftest() if args.selftest else serve())
