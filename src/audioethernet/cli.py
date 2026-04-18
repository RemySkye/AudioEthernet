from __future__ import annotations

import argparse
import socket
import sys

from .config import (
    SUPPORTED_BIT_DEPTHS,
    SUPPORTED_CAPTURE_PROCESSING,
    SUPPORTED_FRAME_MS,
    SUPPORTED_PROFILES,
    SUPPORTED_SAMPLE_RATES,
    StreamConfig,
)
from .logging_setup import configure_logging
from .receiver_app import ReceiverApp
from .sender_app import SenderApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audioethernet",
        description="LAN audio sender/receiver over a single UDP port for Windows 11",
    )

    role = parser.add_mutually_exclusive_group(required=True)
    role.add_argument("-s", "--sender", action="store_true", help="Run as sender")
    role.add_argument("-r", "--receiver", action="store_true", help="Run as receiver")

    parser.add_argument(
        "--bit-depth",
        type=int,
        default=16,
        choices=SUPPORTED_BIT_DEPTHS,
        help="Audio bit depth (default: 16)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48000,
        choices=SUPPORTED_SAMPLE_RATES,
        help="Audio sample rate in Hz (default: 48000)",
    )
    parser.add_argument(
        "--frame-ms",
        type=int,
        default=None,
        choices=SUPPORTED_FRAME_MS,
        help="Frame duration override in milliseconds (defaults from profile)",
    )
    parser.add_argument(
        "-p",
        "--profile",
        default="safe",
        choices=SUPPORTED_PROFILES,
        help="Latency/buffering profile (default: safe)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=50482,
        help="Single UDP port used for discovery and audio (default: 50482)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Custom endpoint name shown in discovery",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    parser.add_argument(
        "--queue-max-frames",
        type=int,
        default=None,
        help="Maximum queued frames for sender capture queue (defaults from profile)",
    )
    parser.add_argument(
        "--receiver-timeout-seconds",
        type=float,
        default=None,
        help="Receiver stream timeout before rediscovery (defaults from profile)",
    )
    parser.add_argument(
        "--sender-peer-timeout-seconds",
        type=float,
        default=None,
        help="Sender timeout to drop inactive receiver targets (defaults from profile)",
    )
    parser.add_argument(
        "--capture-processing",
        default="processed",
        choices=SUPPORTED_CAPTURE_PROCESSING,
        help=(
            "Sender capture mode (default: processed). "
            "Unprocessed uses Stereo Mix / WDM-KS monitor capture and requires "
            "the sender device to be unmuted so the mix contains audio."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    role = "sender" if args.sender else "receiver"
    endpoint_name = args.name if args.name else None

    config = StreamConfig(
        role=role,
        profile=args.profile,
        bit_depth=args.bit_depth,
        sample_rate=args.sample_rate,
        frame_ms=args.frame_ms,
        capture_processing=args.capture_processing,
        port=args.port,
        endpoint_name=endpoint_name or socket.gethostname(),
        receiver_stream_timeout_seconds=args.receiver_timeout_seconds,
        sender_peer_timeout_seconds=args.sender_peer_timeout_seconds,
        queue_max_frames=args.queue_max_frames,
    )
    config.validate()

    logger = configure_logging(args.log_level)

    if role == "sender":
        app = SenderApp(config, logger)
    else:
        app = ReceiverApp(config, logger)

    try:
        app.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
        app.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
