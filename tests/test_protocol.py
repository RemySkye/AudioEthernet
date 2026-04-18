from audioethernet.protocol import (
    FLAG_AUDIO,
    FLAG_HEARTBEAT,
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
