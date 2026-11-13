from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional

SUPPORTED_BIT_DEPTHS = (16, 24, 32)
SUPPORTED_SAMPLE_RATES = (44100, 48000, 96000)
SUPPORTED_FRAME_MS = (2, 5, 10, 20)
SUPPORTED_PROFILES = ("safe", "low")
SUPPORTED_LATENCY_PROFILES = ("low", "balanced", "stable")
SUPPORTED_CAPTURE_PROCESSING = ("unprocessed", "processed", "auto")


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
    jitter_min_frames: int
    jitter_target_frames: int
    jitter_max_frames: int
    jitter_drop_margin: int


PROFILE_SETTINGS = {
    "low": ProfileSettings(
        frame_ms=5,
        queue_max_frames=128,
        heartbeat_seconds=0.75,
        reconnect_seconds=0.5,
        sender_peer_timeout_seconds=6.0,
        receiver_stream_timeout_seconds=3.0,
        capture_latency_seconds=0.005,
        playback_latency_seconds=0.01,
        jitter_min_frames=3,
        jitter_target_frames=4,
        jitter_max_frames=10,
        jitter_drop_margin=10,
    ),
    "balanced": ProfileSettings(
        frame_ms=5,
        queue_max_frames=256,
        heartbeat_seconds=1.0,
        reconnect_seconds=0.75,
        sender_peer_timeout_seconds=8.0,
        receiver_stream_timeout_seconds=4.0,
        capture_latency_seconds=0.01,
        playback_latency_seconds=0.02,
        jitter_min_frames=4,
        jitter_target_frames=6,
        jitter_max_frames=14,
        jitter_drop_margin=12,
    ),
    "stable": ProfileSettings(
        frame_ms=10,
        queue_max_frames=512,
        heartbeat_seconds=1.0,
        reconnect_seconds=1.0,
        sender_peer_timeout_seconds=12.0,
        receiver_stream_timeout_seconds=6.0,
        capture_latency_seconds=0.02,
        playback_latency_seconds=0.03,
        jitter_min_frames=5,
        jitter_target_frames=8,
        jitter_max_frames=18,
        jitter_drop_margin=14,
    ),
}


@dataclass(slots=True)
class StreamConfig:
    role: str
    profile: str = "safe"
    bit_depth: int = 16
    sample_rate: int = 48000
    channels: int = 2
    frame_ms: Optional[int] = None
    capture_processing: str = "processed"
    port: int = 50482
    control_port: int = 50481
    data_port: int = 50482
    endpoint_name: str = socket.gethostname()
    latency_profile: str = "balanced"
    heartbeat_seconds: Optional[float] = None
    reconnect_seconds: Optional[float] = None
    sender_peer_timeout_seconds: Optional[float] = None
    receiver_stream_timeout_seconds: Optional[float] = None
    queue_max_frames: Optional[int] = None
    target_ip: Optional[str] = None
    sender_name: Optional[str] = None
    capture_device: Optional[str] = None
    playback_device: Optional[str] = None
    unprocessed_silence_seconds: float = 3.0

    def __post_init__(self) -> None:
        default_port = 50482
        if self.data_port == default_port and self.port != default_port:
            if 1 <= self.port <= 65535:
                self.data_port = self.port
        elif self.port == default_port and self.data_port != default_port:
            if 1 <= self.data_port <= 65535:
                self.port = self.data_port

        if self.profile == "low" and self.latency_profile == "balanced":
            self.latency_profile = "low"

        defaults = PROFILE_SETTINGS.get(
            self.latency_profile, PROFILE_SETTINGS["balanced"]
        )
        if self.frame_ms is None:
            self.frame_ms = defaults.frame_ms
        if self.queue_max_frames is None:
            self.queue_max_frames = defaults.queue_max_frames
        if self.heartbeat_seconds is None:
            self.heartbeat_seconds = defaults.heartbeat_seconds
        if self.reconnect_seconds is None:
            self.reconnect_seconds = defaults.reconnect_seconds
        if self.sender_peer_timeout_seconds is None:
            self.sender_peer_timeout_seconds = defaults.sender_peer_timeout_seconds
        if self.receiver_stream_timeout_seconds is None:
            self.receiver_stream_timeout_seconds = (
                defaults.receiver_stream_timeout_seconds
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
        if self.channels != 2:
            raise ValueError("only stereo (2 channels) is supported")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be 1..65535")
        if not 1 <= self.control_port <= 65535:
            raise ValueError("control-port must be 1..65535")
        if not 1 <= self.data_port <= 65535:
            raise ValueError("data-port must be 1..65535")
        if self.latency_profile not in SUPPORTED_LATENCY_PROFILES:
            raise ValueError(
                f"latency-profile must be one of {SUPPORTED_LATENCY_PROFILES}"
            )
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
        if self.unprocessed_silence_seconds <= 0:
            raise ValueError("unprocessed-silence-seconds must be greater than 0")

    @property
    def frame_samples(self) -> int:
        assert self.frame_ms is not None
        return int(self.sample_rate * (self.frame_ms / 1000.0))

    @property
    def profile_settings(self) -> ProfileSettings:
        return PROFILE_SETTINGS[self.latency_profile]

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
