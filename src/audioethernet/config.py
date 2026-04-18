from __future__ import annotations

import socket
from dataclasses import dataclass

SUPPORTED_BIT_DEPTHS = (16, 24)
SUPPORTED_SAMPLE_RATES = (44100, 48000)
SUPPORTED_FRAME_MS = (5, 10, 20)
SUPPORTED_LATENCY_PROFILES = ("low", "balanced", "stable")
SUPPORTED_CAPTURE_PROCESSING = ("raw", "processed")


@dataclass(slots=True)
class StreamConfig:
    role: str
    bit_depth: int = 16
    sample_rate: int = 48000
    channels: int = 2
    frame_ms: int = 5
    capture_processing: str = "raw"
    control_port: int = 50481
    data_port: int = 50482
    endpoint_name: str = socket.gethostname()
    latency_profile: str = "balanced"
    heartbeat_seconds: float = 1.0
    reconnect_seconds: float = 1.0
    sender_peer_timeout_seconds: float = 8.0
    receiver_stream_timeout_seconds: float = 3.0
    queue_max_frames: int = 256

    def validate(self) -> None:
        if self.role not in {"sender", "receiver"}:
            raise ValueError("role must be sender or receiver")
        if self.bit_depth not in SUPPORTED_BIT_DEPTHS:
            raise ValueError(f"bit depth must be one of {SUPPORTED_BIT_DEPTHS}")
        if self.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError(f"sample rate must be one of {SUPPORTED_SAMPLE_RATES}")
        if self.frame_ms not in SUPPORTED_FRAME_MS:
            raise ValueError(f"frame-ms must be one of {SUPPORTED_FRAME_MS}")
        if self.channels != 2:
            raise ValueError("only stereo (2 channels) is supported")
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

    @property
    def frame_samples(self) -> int:
        return int(self.sample_rate * (self.frame_ms / 1000.0))

    @property
    def sounddevice_dtype(self) -> str:
        if self.bit_depth == 16:
            return "int16"
        return "int32"

    @property
    def bytes_per_sample(self) -> int:
        if self.bit_depth == 16:
            return 2
        # 24-bit is carried in a 32-bit container for compatibility.
        return 4

    @property
    def frame_bytes(self) -> int:
        return self.frame_samples * self.channels * self.bytes_per_sample


@dataclass(slots=True)
class SenderIdentity:
    name: str
    sample_rate: int
    bit_depth: int
    channels: int
