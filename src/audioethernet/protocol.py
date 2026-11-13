from __future__ import annotations

import struct
from dataclasses import dataclass

from .config import SUPPORTED_BIT_DEPTHS, SUPPORTED_SAMPLE_RATES

MAGIC = b"AETH"
VERSION = 1

FLAG_AUDIO = 0x01
FLAG_HEARTBEAT = 0x02
VALID_FLAGS = FLAG_AUDIO | FLAG_HEARTBEAT

MAX_FRAME_SAMPLES = 16384

HEADER_STRUCT = struct.Struct("!4sBBBBIHIQH")


class ProtocolError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class StreamFormat:
    channels: int
    bit_depth: int
    sample_rate: int
    frame_samples: int

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
    def stream_format(self) -> StreamFormat:
        return StreamFormat(
            channels=self.channels,
            bit_depth=self.bit_depth,
            sample_rate=self.sample_rate,
            frame_samples=self.frame_samples,
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
) -> bytes:
    header = HEADER_STRUCT.pack(
        MAGIC,
        VERSION,
        FLAG_AUDIO,
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
) -> bytes:
    return HEADER_STRUCT.pack(
        MAGIC,
        VERSION,
        FLAG_HEARTBEAT,
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
    if flags == 0 or (flags & ~VALID_FLAGS) != 0:
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

    stream_format = StreamFormat(
        channels=channels,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        frame_samples=frame_samples,
    )
    if (flags & FLAG_HEARTBEAT) and payload_len != 0:
        raise ProtocolError("heartbeat packet must have empty payload")
    if (flags & FLAG_AUDIO) and payload_len == 0:
        raise ProtocolError("audio packet must have non-empty payload")
    _ = stream_format

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
