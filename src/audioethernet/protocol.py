from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"AETH"
VERSION = 1

FLAG_AUDIO = 0x01
FLAG_HEARTBEAT = 0x02

HEADER_STRUCT = struct.Struct("!4sBBBBIHIQH")


class ProtocolError(Exception):
    pass


@dataclass
class Packet:
    flags: int
    channels: int
    bit_depth: int
    sample_rate: int
    frame_samples: int
    sequence: int
    timestamp_samples: int
    payload: bytes


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

    payload = data[HEADER_STRUCT.size :]
    if len(payload) != payload_len:
        raise ProtocolError("payload length mismatch")

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
