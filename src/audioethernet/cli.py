from __future__ import annotations

import argparse
import sys

from .config import PROFILE_SETTINGS, StreamConfig
from .logging_setup import configure_logging
from .receiver_app import ReceiverApp
from .sender_app import SenderApp


class _TrackExplicitAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        explicit = getattr(namespace, "_explicit_args", None)
        if explicit is None:
            explicit = set()
            setattr(namespace, "_explicit_args", explicit)
        explicit.add(self.dest)


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
        choices=[2, 5, 10, 20],
        action=_TrackExplicitAction,
        help="Frame duration in milliseconds (default: profile-defined, 5 ms for balanced/low)",
    )
    parser.add_argument(
        "--latency-profile",
        default="balanced",
        choices=["low", "balanced", "stable"],
        help="Sender: latency and jitter buffer profile (default: balanced). Receivers follow the sender automatically.",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=50481,
        help="Control and discovery UDP port (default: 50481)",
    )
    parser.add_argument(
        "--data-port",
        type=int,
        default=50482,
        help="Receiver data UDP port (default: 50482)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Custom endpoint name shown in discovery",
    )
    parser.add_argument(
        "--target",
        "--sender-ip",
        dest="target_ip",
        default=None,
        help="Receiver: connect directly to a specific sender IP (bypasses broadcast discovery)",
    )
    parser.add_argument(
        "--sender-name",
        default=None,
        help="Receiver: filter discovered senders by name substring",
    )
    parser.add_argument(
        "--capture-device",
        default=None,
        help="Sender: audio capture device index or substring match",
    )
    parser.add_argument(
        "--playback-device",
        default=None,
        help="Receiver: audio playback device index or substring match",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--queue-max-frames",
        type=int,
        default=256,
        action=_TrackExplicitAction,
        help="Maximum queued frames for sender capture queue",
    )
    parser.add_argument(
        "--receiver-timeout-seconds",
        type=float,
        default=4.0,
        action=_TrackExplicitAction,
        help="Receiver stream timeout before rediscovery",
    )
    parser.add_argument(
        "--sender-peer-timeout-seconds",
        type=float,
        default=8.0,
        action=_TrackExplicitAction,
        help="Sender timeout to drop inactive receiver targets",
    )
    parser.add_argument(
        "--capture-processing",
        default="processed",
        choices=["processed", "unprocessed", "auto"],
        help=(
            "Sender capture mode (default: processed). "
            "Processed captures regular Windows loopback audio. "
            "Unprocessed uses Stereo Mix / WDM-KS monitor capture without Windows APO/loopback processing. "
            "Auto tries unprocessed first and falls back to processed if unavailable or silent."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    role = "sender" if args.sender else "receiver"
    explicit = getattr(args, "_explicit_args", set())
    profile_defaults = PROFILE_SETTINGS[args.latency_profile]

    frame_ms = (
        args.frame_ms if "frame_ms" in explicit else profile_defaults.frame_ms
    )
    queue_max_frames = (
        args.queue_max_frames
        if "queue_max_frames" in explicit
        else profile_defaults.queue_max_frames
    )
    receiver_timeout = (
        args.receiver_timeout_seconds
        if "receiver_timeout_seconds" in explicit
        else profile_defaults.receiver_stream_timeout_seconds
    )
    sender_peer_timeout = (
        args.sender_peer_timeout_seconds
        if "sender_peer_timeout_seconds" in explicit
        else profile_defaults.sender_peer_timeout_seconds
    )

    config = StreamConfig(
        role=role,
        bit_depth=args.bit_depth,
        sample_rate=args.sample_rate,
        frame_ms=frame_ms,
        capture_processing=args.capture_processing,
        control_port=args.control_port,
        data_port=args.data_port,
        endpoint_name=args.name or StreamConfig(role=role).endpoint_name,
        latency_profile=args.latency_profile,
        receiver_stream_timeout_seconds=receiver_timeout,
        sender_peer_timeout_seconds=sender_peer_timeout,
        queue_max_frames=queue_max_frames,
        target_ip=args.target_ip,
        sender_name=args.sender_name,
        capture_device=args.capture_device,
        playback_device=args.playback_device,
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
