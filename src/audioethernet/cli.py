from __future__ import annotations

import argparse
import sys

from .config import StreamConfig
from .logging_setup import configure_logging
from .receiver_app import ReceiverApp
from .sender_app import SenderApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audioethernet",
        description="LAN audio sender/receiver for Windows 11",
    )

    role = parser.add_mutually_exclusive_group(required=True)
    role.add_argument("-s", "--sender", action="store_true", help="Run as sender")
    role.add_argument("-r", "--receiver", action="store_true", help="Run as receiver")

    parser.add_argument(
        "--bit-depth",
        type=int,
        default=16,
        choices=[16, 24, 32],
        help="Audio bit depth (default: 16)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48000,
        choices=[44100, 48000, 96000],
        help="Audio sample rate in Hz (default: 48000)",
    )
    parser.add_argument(
        "--frame-ms",
        type=int,
        default=5,
        choices=[5, 10, 20],
        help="Frame duration in milliseconds (default: 5)",
    )
    parser.add_argument(
        "--latency-profile",
        default="balanced",
        choices=["low", "balanced", "stable"],
        help="Jitter target profile for receiver (default: balanced)",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=50481,
        help="Control and discovery UDP port",
    )
    parser.add_argument(
        "--data-port",
        type=int,
        default=50482,
        help="Receiver data UDP port",
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
        default=256,
        help="Maximum queued frames for sender capture queue",
    )
    parser.add_argument(
        "--receiver-timeout-seconds",
        type=float,
        default=3.0,
        help="Receiver stream timeout before rediscovery",
    )
    parser.add_argument(
        "--sender-peer-timeout-seconds",
        type=float,
        default=8.0,
        help="Sender timeout to drop inactive receiver targets",
    )
    parser.add_argument(
        "--capture-processing",
        default="unprocessed",
        choices=["unprocessed", "processed"],
        help=(
            "Sender capture mode (default: unprocessed). "
            "Unprocessed uses Stereo Mix / WDM-KS monitor capture and requires "
            "the sender device to be unmuted so the mix contains audio. "
            "Use processed to include endpoint effects such as APO processing."
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
        bit_depth=args.bit_depth,
        sample_rate=args.sample_rate,
        frame_ms=args.frame_ms,
        capture_processing=args.capture_processing,
        control_port=args.control_port,
        data_port=args.data_port,
        endpoint_name=endpoint_name or StreamConfig(role=role).endpoint_name,
        latency_profile=args.latency_profile,
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
