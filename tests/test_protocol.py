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
