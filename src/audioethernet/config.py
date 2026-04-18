from __future__ import annotations

import socket
from dataclasses import dataclass

SUPPORTED_BIT_DEPTHS = (16, 24, 32)
SUPPORTED_SAMPLE_RATES = (32000, 44100, 48000, 88200, 96000)
SUPPORTED_FRAME_MS = (10, 20)
SUPPORTED_PROFILES = ("safe", "low")
SUPPORTED_CAPTURE_PROCESSING = ("processed", "unprocessed")


@dataclass(frozen=True, slots=True)
class ProfileSettings:
    frame_ms: int
    queue_max_frames: int
    heartbeat_seconds: float
    reconnect_seconds: float
    sender_peer_timeout_seconds: float
    receiver_stream_timeout_seconds: float
    capture_latency_seconds: float
    playback_latency_seconds: float
    jitter_target_frames: int
    jitter_drop_margin: int


PROFILE_SETTINGS = {
    "safe": ProfileSettings(
        frame_ms=20,
        queue_max_frames=512,
        heartbeat_seconds=1.0,
        reconnect_seconds=1.0,
        sender_peer_timeout_seconds=12.0,
        receiver_stream_timeout_seconds=6.0,
        capture_latency_seconds=0.10,
        playback_latency_seconds=0.10,
        jitter_target_frames=8,
        jitter_drop_margin=12,
    ),
    "low": ProfileSettings(
        frame_ms=10,
        queue_max_frames=256,
        heartbeat_seconds=0.75,
        reconnect_seconds=0.5,
        sender_peer_timeout_seconds=6.0,
        receiver_stream_timeout_seconds=3.0,
        capture_latency_seconds=0.03,
        playback_latency_seconds=0.03,
        jitter_target_frames=4,
        jitter_drop_margin=6,
    ),
}


@dataclass(slots=True)
class StreamConfig:
    role: str
    profile: str = "safe"
    bit_depth: int = 16
    sample_rate: int = 48000
    channels: int = 2
    frame_ms: int | None = None
    capture_processing: str = "processed"
    control_port: int = 50481
    data_port: int = 0
    endpoint_name: str = socket.gethostname()
    queue_max_frames: int | None = None
    heartbeat_seconds: float | None = None
    reconnect_seconds: float | None = None
    sender_peer_timeout_seconds: float | None = None
    receiver_stream_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        settings = PROFILE_SETTINGS.get(self.profile)
        if settings is None:
            return

        if self.frame_ms is None:
            self.frame_ms = settings.frame_ms
        if self.queue_max_frames is None:
            self.queue_max_frames = settings.queue_max_frames
        if self.heartbeat_seconds is None:
            self.heartbeat_seconds = settings.heartbeat_seconds
        if self.reconnect_seconds is None:
            self.reconnect_seconds = settings.reconnect_seconds
        if self.sender_peer_timeout_seconds is None:
            self.sender_peer_timeout_seconds = settings.sender_peer_timeout_seconds
        if self.receiver_stream_timeout_seconds is None:
            self.receiver_stream_timeout_seconds = (
                settings.receiver_stream_timeout_seconds
            )

    def validate(self) -> None:
        if self.role not in {"sender", "receiver"}:
            raise ValueError("role must be sender or receiver")
        if self.profile not in SUPPORTED_PROFILES:
            raise ValueError(f"profile must be one of {SUPPORTED_PROFILES}")
        if self.bit_depth not in SUPPORTED_BIT_DEPTHS:
            raise ValueError(f"bit depth must be one of {SUPPORTED_BIT_DEPTHS}")
        if self.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError(f"sample rate must be one of {SUPPORTED_SAMPLE_RATES}")
        if self.frame_ms not in SUPPORTED_FRAME_MS:
            raise ValueError(f"frame-ms must be one of {SUPPORTED_FRAME_MS}")
        if (self.sample_rate * self.frame_ms) % 1000 != 0:
            raise ValueError(
                "frame-ms must produce whole samples at the selected sample rate"
            )
        if self.channels != 2:
            raise ValueError("only stereo (2 channels) is supported")
        if not 1 <= self.control_port <= 65535:
            raise ValueError("control-port must be 1..65535")
        if not 0 <= self.data_port <= 65535:
            raise ValueError("data-port must be 0..65535")
        if self.capture_processing not in SUPPORTED_CAPTURE_PROCESSING:
            raise ValueError(
                f"capture-processing must be one of {SUPPORTED_CAPTURE_PROCESSING}"
            )
        if self.queue_max_frames is None or self.queue_max_frames < 1:
            raise ValueError("queue-max-frames must be 1 or greater")
        if self.heartbeat_seconds is None or self.heartbeat_seconds <= 0:
            raise ValueError("heartbeat-seconds must be greater than 0")
        if self.reconnect_seconds is None or self.reconnect_seconds <= 0:
            raise ValueError("reconnect-seconds must be greater than 0")
        if (
            self.sender_peer_timeout_seconds is None
            or self.sender_peer_timeout_seconds <= 0
        ):
            raise ValueError("sender-peer-timeout-seconds must be greater than 0")
        if (
            self.receiver_stream_timeout_seconds is None
            or self.receiver_stream_timeout_seconds <= 0
        ):
            raise ValueError("receiver-timeout-seconds must be greater than 0")

    @property
    def frame_samples(self) -> int:
        return (self.sample_rate * self.frame_ms) // 1000

    @property
    def profile_settings(self) -> ProfileSettings:
        settings = PROFILE_SETTINGS.get(self.profile)
        if settings is None:
            raise ValueError(f"profile must be one of {SUPPORTED_PROFILES}")
        return settings

    @property
    def sounddevice_dtype(self) -> str:
        if self.bit_depth == 16:
            return "int16"
        return "int32"

    @property
    def bytes_per_sample(self) -> int:
        if self.bit_depth == 16:
            return 2
        # 24-bit and 32-bit are carried in a 32-bit container for compatibility.
        return 4

    @property
    def frame_bytes(self) -> int:
        return self.frame_samples * self.channels * self.bytes_per_sample
