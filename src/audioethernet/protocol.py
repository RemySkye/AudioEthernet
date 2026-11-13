from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from .config import (
    SUPPORTED_BIT_DEPTHS,
    SUPPORTED_LATENCY_PROFILES,
    SUPPORTED_SAMPLE_RATES,
)

MAGIC = b"AETH"
VERSION = 1

FLAG_AUDIO = 0x01
FLAG_HEARTBEAT = 0x02
FLAG_PROFILE_MASK = 0x0C
FLAG_PROFILE_SHIFT = 2
VALID_FLAGS = FLAG_AUDIO | FLAG_HEARTBEAT | FLAG_PROFILE_MASK

_PROFILE_TO_CODE = {
    "low": 1,
    "balanced": 2,
    "stable": 3,
}
_CODE_TO_PROFILE = {code: name for name, code in _PROFILE_TO_CODE.items()}

MAX_FRAME_SAMPLES = 16384

HEADER_STRUCT = struct.Struct("!4sBBBBIHIQH")


class ProtocolError(Exception):
    pass


def encode_latency_profile_flags(latency_profile: Optional[str]) -> int:
    if latency_profile is None:
        return 0
    code = _PROFILE_TO_CODE.get(latency_profile, 0)
    return (code << FLAG_PROFILE_SHIFT) & FLAG_PROFILE_MASK


def decode_latency_profile_flags(flags: int) -> Optional[str]:
    code = (flags & FLAG_PROFILE_MASK) >> FLAG_PROFILE_SHIFT
    return _CODE_TO_PROFILE.get(code)


@dataclass(slots=True, frozen=True)
class StreamFormat:
    channels: int
    bit_depth: int
    sample_rate: int
    frame_samples: int
    latency_profile: Optional[str] = None

    @property
    def bytes_per_sample(self) -> int:
        if self.bit_depth == 16:
            return 2
        return 4

    @property
    def frame_bytes(self) -> int:
        return self.frame_samples * self.channels * self.bytes_per_sample


@dataclass(slots=True)
class Packet:
    flags: int
    channels: int
    bit_depth: int
    sample_rate: int
    frame_samples: int
    sequence: int
    timestamp_samples: int
    payload: bytes

    @property
    def latency_profile(self) -> Optional[str]:
        return decode_latency_profile_flags(self.flags)

    @property
    def stream_format(self) -> StreamFormat:
        return StreamFormat(
            channels=self.channels,
            bit_depth=self.bit_depth,
            sample_rate=self.sample_rate,
            frame_samples=self.frame_samples,
            latency_profile=self.latency_profile,
        )

    @property
    def frame_bytes(self) -> int:
        return len(self.payload)


def pack_audio_packet(
    *,
    channels: int,
    bit_depth: int,
    sample_rate: int,
    frame_samples: int,
    sequence: int,
    timestamp_samples: int,
    payload: bytes,
    latency_profile: Optional[str] = None,
) -> bytes:
    if (
        latency_profile is not None
        and latency_profile not in SUPPORTED_LATENCY_PROFILES
    ):
        raise ValueError(f"Unsupported latency profile: {latency_profile}")
    flags = FLAG_AUDIO | encode_latency_profile_flags(latency_profile)
    header = HEADER_STRUCT.pack(
        MAGIC,
        VERSION,
        flags,
        channels,
        bit_depth,
        sample_rate,
        frame_samples,
        sequence,
        timestamp_samples,
        len(payload),
    )
    return header + payload


def pack_heartbeat(
    *,
    channels: int,
    bit_depth: int,
    sample_rate: int,
    frame_samples: int,
    sequence: int,
    timestamp_samples: int,
    latency_profile: Optional[str] = None,
) -> bytes:
    if (
        latency_profile is not None
        and latency_profile not in SUPPORTED_LATENCY_PROFILES
    ):
        raise ValueError(f"Unsupported latency profile: {latency_profile}")
    flags = FLAG_HEARTBEAT | encode_latency_profile_flags(latency_profile)
    return HEADER_STRUCT.pack(
        MAGIC,
        VERSION,
        flags,
        channels,
        bit_depth,
        sample_rate,
        frame_samples,
        sequence,
        timestamp_samples,
        0,
    )


def unpack_packet(data: bytes) -> Packet:
    if len(data) < HEADER_STRUCT.size:
        raise ProtocolError("packet too small")

    (
        magic,
        version,
        flags,
        channels,
        bit_depth,
        sample_rate,
        frame_samples,
        sequence,
        timestamp_samples,
        payload_len,
    ) = HEADER_STRUCT.unpack_from(data)

    if magic != MAGIC:
        raise ProtocolError("invalid magic")
    if version != VERSION:
        raise ProtocolError("unsupported protocol version")
    if (flags & (FLAG_AUDIO | FLAG_HEARTBEAT)) == 0 or (flags & ~VALID_FLAGS) != 0:
        raise ProtocolError("invalid packet flags")
    if channels != 2:
        raise ProtocolError("unsupported channel count")
    if bit_depth not in SUPPORTED_BIT_DEPTHS:
        raise ProtocolError("unsupported bit depth")
    if sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise ProtocolError("unsupported sample rate")
    if not 1 <= frame_samples <= MAX_FRAME_SAMPLES:
        raise ProtocolError("invalid frame sample count")

    payload = data[HEADER_STRUCT.size :]
    if len(payload) != payload_len:
        raise ProtocolError("payload length mismatch")

    if (flags & FLAG_HEARTBEAT) and payload_len != 0:
        raise ProtocolError("heartbeat packet must have empty payload")
    if (flags & FLAG_AUDIO) and payload_len == 0:
        raise ProtocolError("audio packet must have non-empty payload")

    return Packet(
        flags=flags,
        channels=channels,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        frame_samples=frame_samples,
        sequence=sequence,
        timestamp_samples=timestamp_samples,
        payload=payload,
    )
