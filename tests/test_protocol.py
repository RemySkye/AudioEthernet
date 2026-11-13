from audioethernet.protocol import (
    FLAG_AUDIO,
    FLAG_HEARTBEAT,
    HEADER_STRUCT,
    MAGIC,
    VERSION,
    ProtocolError,
    pack_audio_packet,
    pack_heartbeat,
    unpack_packet,
)


def test_audio_packet_round_trip() -> None:
    payload = b"abc123"
    packet_bytes = pack_audio_packet(
        channels=2,
        bit_depth=16,
        sample_rate=48000,
        frame_samples=480,
        sequence=42,
        timestamp_samples=1337,
        payload=payload,
    )

    packet = unpack_packet(packet_bytes)
    assert packet.flags == FLAG_AUDIO
    assert packet.channels == 2
    assert packet.bit_depth == 16
    assert packet.sample_rate == 48000
    assert packet.frame_samples == 480
    assert packet.sequence == 42
    assert packet.timestamp_samples == 1337
    assert packet.payload == payload
    assert packet.frame_bytes == len(payload)
    assert packet.stream_format.frame_bytes == 480 * 2 * 2


def test_heartbeat_packet_has_empty_payload() -> None:
    packet_bytes = pack_heartbeat(
        channels=2,
        bit_depth=16,
        sample_rate=48000,
        frame_samples=480,
        sequence=7,
        timestamp_samples=9000,
    )

    packet = unpack_packet(packet_bytes)
    assert packet.flags == FLAG_HEARTBEAT
    assert packet.payload == b""


def test_stream_format_handles_32_bit_payload_sizes() -> None:
    packet_bytes = pack_audio_packet(
        channels=2,
        bit_depth=32,
        sample_rate=96000,
        frame_samples=960,
        sequence=9,
        timestamp_samples=777,
        payload=b"x" * (960 * 2 * 4),
    )

    packet = unpack_packet(packet_bytes)
    assert packet.stream_format.channels == 2
    assert packet.stream_format.bit_depth == 32
    assert packet.stream_format.sample_rate == 96000
    assert packet.stream_format.frame_samples == 960
    assert packet.stream_format.frame_bytes == 960 * 2 * 4


def test_unpack_packet_rejects_invalid_header_values() -> None:
    bad_rate = HEADER_STRUCT.pack(
        MAGIC, VERSION, FLAG_HEARTBEAT, 2, 16, 12345, 480, 1, 0, 0
    )
    bad_channels = HEADER_STRUCT.pack(
        MAGIC, VERSION, FLAG_HEARTBEAT, 99, 16, 48000, 480, 1, 0, 0
    )
    bad_depth = HEADER_STRUCT.pack(
        MAGIC, VERSION, FLAG_HEARTBEAT, 2, 8, 48000, 480, 1, 0, 0
    )
    bad_flags = HEADER_STRUCT.pack(
        MAGIC, VERSION, 0x80, 2, 16, 48000, 480, 1, 0, 0
    )

    for raw in (bad_rate, bad_channels, bad_depth, bad_flags):
        try:
            unpack_packet(raw)
            raised = False
        except ProtocolError:
            raised = True
        assert raised


def test_packet_round_trips_latency_profile() -> None:
    for profile in ("low", "balanced", "stable"):
        hb = pack_heartbeat(
            channels=2,
            bit_depth=24,
            sample_rate=48000,
            frame_samples=240,
            sequence=1,
            timestamp_samples=0,
            latency_profile=profile,
        )
        unpacked = unpack_packet(hb)
        assert unpacked.latency_profile == profile
        assert unpacked.stream_format.latency_profile == profile
