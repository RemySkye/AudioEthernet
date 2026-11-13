from audioethernet.jitter_buffer import AdaptiveJitterBuffer


def test_jitter_buffer_reorders_out_of_order_packets() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=32
    )
    # low profile starts with target_frames=3
    assert buf.push(b"aaaa", sequence=0) == 0
    assert buf.push(b"cccc", sequence=2) == 0
    assert buf.push(b"bbbb", sequence=1) == 0

    f0, d0, u0 = buf.pop()
    f1, d1, u1 = buf.pop()
    f2, d2, u2 = buf.pop()

    assert (f0, d0, u0) == (b"aaaa", 0, False)
    assert (f1, d1, u1) == (b"bbbb", 0, False)
    assert (f2, d2, u2) == (b"cccc", 0, False)


def test_jitter_buffer_ignores_duplicates_and_late_packets() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=32
    )
    buf.push(b"aaaa", sequence=10)
    buf.push(b"aaaa", sequence=10)  # duplicate
    buf.push(b"bbbb", sequence=11)
    buf.push(b"cccc", sequence=12)

    assert buf.depth() == 3
    frame, _, underrun = buf.pop()
    assert frame == b"aaaa"
    assert not underrun

    # Late packet before next_seq=11 is dropped
    assert buf.push(b"late", sequence=9) == 1
    assert buf.packet_counters() == (0, 1)


def test_jitter_buffer_handles_missing_packet_and_sequence_wrap() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=32
    )
    start = 0xFFFFFFFE
    buf.push(b"f_fe", sequence=start)
    buf.push(b"f_ff", sequence=0xFFFFFFFF)
    # Skip sequence 0
    buf.push(b"f_01", sequence=1)

    assert buf.pop() == (b"f_fe", 0, False)
    assert buf.pop() == (b"f_ff", 0, False)

    # Slot 0 is missing -> silence + lost packet count = 1
    f_miss, _, u_miss = buf.pop()
    assert f_miss == b"\x00\x00\x00\x00"
    assert u_miss is True
    assert buf.packet_counters()[0] == 1

    # Slot 1 plays cleanly on next tick
    assert buf.pop() == (b"f_01", 0, False)


def test_jitter_buffer_resets_when_sender_restarts_sequence() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=16
    )
    for seq in range(1000, 1003):
        buf.push(b"old1", sequence=seq)
    assert buf.pop()[0] == b"old1"

    # Sender restarted at sequence 0
    buf.push(b"new0", sequence=0)
    buf.push(b"new1", sequence=1)
    buf.push(b"new2", sequence=2)

    assert buf.pop()[0] == b"new0"
    assert buf.pop()[0] == b"new1"
    assert buf.pop()[0] == b"new2"


def test_jitter_buffer_adapts_target_on_underrun_and_stable_playback() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=64
    )
    initial_target = buf.target_frames()
    assert initial_target == 3

    for seq in range(3):
        buf.push(b"abcd", sequence=seq)
    for _ in range(3):
        assert buf.pop()[2] is False

    # Next pop hits empty buffer -> underrun bumps target
    _, _, underrun = buf.pop()
    assert underrun is True
    assert buf.target_frames() == 4

    # Sustained clean pops eventually step target back down
    for seq in range(3, 420):
        buf.push(b"abcd", sequence=seq)
        if seq >= 6:
            buf.pop()

    assert buf.target_frames() < 4


def test_jitter_buffer_scales_timing_for_chunked_subframes() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=960, latency_profile="low", max_frames=64
    )
    assert buf.target_frames() == 3  # 5ms base

    buf.configure_timing(0.0025)  # 2.5ms sub-frame chunks -> 2x frame count
    assert buf.target_frames() == 6
