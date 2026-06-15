"""CLI entrypoint for the Tuya PSK Bridge.

Usage::

    python -m tuya_psk_bridge.main --config bridge_config.yaml
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .config import load_config
from .mqtt_runtime import BridgeRuntime
from .psk_frontend import DEFAULT_HINT

logger = logging.getLogger(__name__)

# The PSK hint used by Tuya devices — stable across the observed firmware.
# In a future version this may become per-device or configurable.
_PSK_HINT = DEFAULT_HINT


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuya-psk-bridge",
        description="Tuya PSK Local Cloud Bridge — decode Tuya device payloads and publish to Home Assistant via MQTT discovery.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the bridge YAML configuration file.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the log level from the config file (e.g. DEBUG, INFO).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, load config, start the bridge, and block until interrupted.

    Returns an exit code (0 for clean shutdown, 1 for error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Load configuration.
    try:
        config = load_config(args.config)
    except (FileNotFoundError, Exception) as exc:
        logger.error("Failed to load config from %s: %s", args.config, exc)
        return 1

    # Set up logging.
    log_level = args.log_level or config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Build and start the runtime.
    runtime = BridgeRuntime(config=config, psk_hint=_PSK_HINT)

    def _signal_handler(signum: int, _frame: object) -> None:
        logger.info("Received signal %d; shutting down", signum)
        runtime.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        runtime.start()
        # Block until the server thread exits (stopped by signal or runtime.stop()).
        runtime._server._thread.join() if runtime._server and runtime._server._thread else None
    except KeyboardInterrupt:
        runtime.stop()
    except Exception as exc:
        logger.error("Bridge runtime error: %s", exc)
        runtime.stop()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
