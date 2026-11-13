import numpy as np

from audioethernet.jitter_buffer import AdaptiveJitterBuffer


def test_jitter_buffer_reorders_out_of_order_packets() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=32
    )
    # low profile starts with target_frames=4
    assert buf.push(b"aaaa", sequence=0) == 0
    assert buf.push(b"cccc", sequence=2) == 0
    assert buf.push(b"dddd", sequence=3) == 0
    assert buf.push(b"bbbb", sequence=1) == 0

    f0, d0, u0 = buf.pop()
    f1, d1, u1 = buf.pop()
    f2, d2, u2 = buf.pop()
    f3, d3, u3 = buf.pop()

    assert (f0, d0, u0) == (b"aaaa", 0, False)
    assert (f1, d1, u1) == (b"bbbb", 0, False)
    assert (f2, d2, u2) == (b"cccc", 0, False)
    assert (f3, d3, u3) == (b"dddd", 0, False)


def test_jitter_buffer_ignores_duplicates_and_late_packets() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=32
    )
    buf.push(b"aaaa", sequence=10)
    buf.push(b"aaaa", sequence=10)  # duplicate
    buf.push(b"bbbb", sequence=11)
    buf.push(b"cccc", sequence=12)
    buf.push(b"dddd", sequence=13)

    assert buf.depth() == 4
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
    buf.push(b"f_02", sequence=2)

    assert buf.pop() == (b"f_fe", 0, False)
    assert buf.pop() == (b"f_ff", 0, False)

    # Slot 0 is missing -> silence + lost packet count = 1
    f_miss, _, u_miss = buf.pop()
    assert f_miss == b"\x00\x00\x00\x00"
    assert u_miss is True
    assert buf.packet_counters()[0] == 1

    # Slot 1 plays cleanly on next tick without blocking for a full re-prime
    assert buf.pop() == (b"f_01", 0, False)


def test_jitter_buffer_resets_when_sender_restarts_sequence() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=16
    )
    for seq in range(1000, 1004):
        buf.push(b"old1", sequence=seq)
    assert buf.pop()[0] == b"old1"

    # Sender restarted at sequence 0
    for seq in range(4):
        buf.push(f"new{seq}".encode("ascii"), sequence=seq)

    assert buf.pop()[0] == b"new0"
    assert buf.pop()[0] == b"new1"
    assert buf.pop()[0] == b"new2"
    assert buf.pop()[0] == b"new3"


def test_jitter_buffer_adapts_target_once_per_underrun_streak_and_resumes_immediately() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=4, latency_profile="low", max_frames=64
    )
    initial_target = buf.target_frames()
    assert initial_target == 4

    for seq in range(4):
        buf.push(b"abcd", sequence=seq)
    for _ in range(4):
        assert buf.pop()[2] is False

    # Consecutive underruns in one streak grow target by +1 total (not +2)
    assert buf.pop()[2] is True
    assert buf.target_frames() == 5
    assert buf.pop()[2] is True
    assert buf.target_frames() == 5

    # Next packet plays immediately without re-priming a multi-frame silence gap
    buf.push(b"efgh", sequence=4)
    f_next, _, u_next = buf.pop()
    assert f_next == b"efgh"
    assert u_next is False

    # Sustained clean pops eventually step target back down
    for seq in range(5, 1100):
        buf.push(b"abcd", sequence=seq)
        buf.pop()

    assert buf.target_frames() < 5


def test_jitter_buffer_soft_ramps_pcm_on_underrun_and_recovery() -> None:
    # 60 samples * 2ch * 2bytes = 240 bytes (2.5ms at 24kHz / 48kHz test frame)
    tone = np.full((60, 2), 10000, dtype=np.int16).tobytes()
    buf = AdaptiveJitterBuffer(
        frame_bytes=len(tone),
        latency_profile="low",
        max_frames=32,
        bit_depth=16,
        channels=2,
    )
    for seq in range(4):
        buf.push(tone, sequence=seq)
    for _ in range(4):
        buf.pop()

    # First underrun tapers smoothly from 10000 down to 0 instead of dropping directly to 0
    plc_bytes, _, u1 = buf.pop()
    assert u1 is True
    plc_arr = np.frombuffer(plc_bytes, dtype=np.int16).reshape(-1, 2)
    assert plc_arr[0, 0] == 10000
    assert plc_arr[-1, 0] == 0

    # Recovery fades head smoothly up from 0 to 10000
    buf.push(tone, sequence=4)
    rec_bytes, _, u2 = buf.pop()
    assert u2 is False
    rec_arr = np.frombuffer(rec_bytes, dtype=np.int16).reshape(-1, 2)
    assert rec_arr[0, 0] == 0
    assert rec_arr[-1, 0] == 10000


def test_jitter_buffer_scales_timing_for_chunked_subframes() -> None:
    buf = AdaptiveJitterBuffer(
        frame_bytes=960, latency_profile="low", max_frames=64
    )
    assert buf.target_frames() == 4  # 5ms base

    buf.configure_timing(0.0025)  # 2.5ms sub-frame chunks -> 2x frame count
    assert buf.target_frames() == 8
