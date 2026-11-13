import numpy as np

import audioethernet.audio_capture as audio_capture
from audioethernet.audio_capture import LoopbackCapture
from audioethernet.audio_playback import AudioPlayback
from audioethernet.config import StreamConfig


class DummyLogger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass

    def error(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


def test_float_to_pcm_bytes_24bit_is_msb_aligned_int32() -> None:
    config = StreamConfig(role="sender", bit_depth=24)
    capture = LoopbackCapture(
        config=config,
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )

    samples = np.array([[1.0, -1.0], [0.5, -0.5]], dtype=np.float32)
    raw = capture._float_to_pcm_bytes(samples)
    decoded = np.frombuffer(raw, dtype=np.int32).reshape(-1, 2)

    assert decoded[0, 0] == (8388607 << 8)
    assert decoded[0, 1] == (-8388607 << 8)
    assert abs(decoded[1, 0] - ((4194304) << 8)) <= 256


def test_float_to_pcm_bytes_16bit_and_32bit() -> None:
    samples = np.array([[1.0, -1.0]], dtype=np.float32)

    cap16 = LoopbackCapture(
        config=StreamConfig(role="sender", bit_depth=16),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )
    dec16 = np.frombuffer(cap16._float_to_pcm_bytes(samples), dtype=np.int16)
    assert list(dec16) == [32767, -32767]

    cap32 = LoopbackCapture(
        config=StreamConfig(role="sender", bit_depth=32),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )
    dec32 = np.frombuffer(cap32._float_to_pcm_bytes(samples), dtype=np.int32)
    assert list(dec32) == [2147483647, -2147483647]


def test_select_unprocessed_device_by_name_and_index(monkeypatch) -> None:
    fake_hostapis = [
        {"name": "MME"},
        {"name": "Windows WDM-KS"},
    ]
    fake_devices = [
        {"name": "Stereo Mix (Realtek)", "max_input_channels": 2, "hostapi": 0},
        {"name": "Microphone (USB)", "max_input_channels": 2, "hostapi": 1},
        {"name": "Stereo Mix (Realtek)", "max_input_channels": 2, "hostapi": 1},
    ]
    monkeypatch.setattr(audio_capture.sd, "query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr(audio_capture.sd, "query_devices", lambda: fake_devices)

    cap_sub = LoopbackCapture(
        config=StreamConfig(
            role="sender",
            capture_processing="unprocessed",
            capture_device="Stereo Mix",
        ),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )
    # Prefers Windows WDM-KS match (index 2) over MME (index 0)
    assert cap_sub._select_unprocessed_input_device() == (2, "Stereo Mix (Realtek)")

    cap_idx = LoopbackCapture(
        config=StreamConfig(
            role="sender",
            capture_processing="unprocessed",
            capture_device="1",
        ),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )
    assert cap_idx._select_unprocessed_input_device() == (1, "Microphone (USB)")


def test_playback_callback_handles_exact_short_long_and_empty_frames() -> None:
    frames: list[bytes] = [b"", b"ab", b"abcd", b"abcdef"]
    playback = AudioPlayback(
        config=StreamConfig(role="receiver"),
        frame_provider=lambda: frames.pop(0),
        logger=DummyLogger(),
    )

    out = bytearray(4)
    playback._audio_callback(out, 1, None, None)
    assert out == bytearray(b"\x00\x00\x00\x00")

    playback._audio_callback(out, 1, None, None)
    assert out == bytearray(b"ab\x00\x00")

    playback._audio_callback(out, 1, None, None)
    assert out == bytearray(b"abcd")

    playback._audio_callback(out, 1, None, None)
    assert out == bytearray(b"abcd")


def test_playback_prefers_wasapi_default_output(monkeypatch) -> None:
    import audioethernet.audio_playback as audio_playback

    fake_hostapis = [
        {"name": "MME", "default_output_device": 0},
        {"name": "Windows WASAPI", "default_output_device": 1},
    ]
    fake_devices = [
        {"name": "Speakers (Realtek)", "max_output_channels": 2, "hostapi": 0},
        {"name": "Speakers (Realtek)", "max_output_channels": 2, "hostapi": 1},
    ]
    monkeypatch.setattr(audio_playback.sd, "query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr(audio_playback.sd, "query_devices", lambda: fake_devices)

    playback = AudioPlayback(
        config=StreamConfig(role="receiver"),
        frame_provider=lambda: b"",
        logger=DummyLogger(),
    )
    assert playback._resolve_output_device(2) == (1, True)


def test_processed_recorder_emits_paced_silence_when_wasapi_idle() -> None:
    capture = LoopbackCapture(
        config=StreamConfig(role="sender", frame_ms=5, sample_rate=48000, bit_depth=16),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )
    emitted: list[tuple[bytes, int]] = []

    class FakeIdleRecorder:
        def _capture_available_frames(self) -> int:
            return 0

        def record(self, numframes=None):
            raise AssertionError("Should not block in record() while idle")

    def on_frame(data: bytes, n: int) -> None:
        emitted.append((data, n))
        if len(emitted) >= 3:
            capture._stop_event.set()

    capture._on_frame = on_frame
    capture._drain_processed_recorder(FakeIdleRecorder(), 240)
    assert len(emitted) >= 3
    assert all(len(d) == 240 * 2 * 2 and n == 240 for d, n in emitted)


def test_select_unprocessed_prefers_default_speaker_virtual_cable_pair(monkeypatch) -> None:
    fake_hostapis = [{"name": "Windows WDM-KS"}]
    fake_devices = [
        {
            "name": "Stereo Mix (Realtek HD Audio Stereo input)",
            "max_input_channels": 2,
            "hostapi": 0,
        },
        {"name": "CABLE Output (VB-Audio Point)", "max_input_channels": 8, "hostapi": 0},
    ]
    monkeypatch.setattr(audio_capture.sd, "query_hostapis", lambda: fake_hostapis)
    monkeypatch.setattr(audio_capture.sd, "query_devices", lambda: fake_devices)

    class FakeSpeaker:
        def __init__(self, name: str) -> None:
            self.name = name

    cap = LoopbackCapture(
        config=StreamConfig(role="sender", capture_processing="unprocessed"),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )

    # When default speaker is CABLE Input, match CABLE Output instead of Stereo Mix.
    monkeypatch.setattr(
        audio_capture.sc,
        "default_speaker",
        lambda: FakeSpeaker("CABLE Input (VB-Audio Virtual Cable)"),
    )
    assert cap._select_unprocessed_input_device() == (1, "CABLE Output (VB-Audio Point)")

    # When default speaker is Realtek hardware speakers, fall back to Stereo Mix.
    monkeypatch.setattr(
        audio_capture.sc,
        "default_speaker",
        lambda: FakeSpeaker("Speakers (2- Realtek(R) Audio)"),
    )
    assert cap._select_unprocessed_input_device() == (
        0,
        "Stereo Mix (Realtek HD Audio Stereo input)",
    )


def test_open_unprocessed_stream_falls_back_to_blocksize_zero(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeStream:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)
            if kwargs["blocksize"] != 0:
                raise audio_capture.sd.PortAudioError("fixed blocksize unsupported")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(audio_capture.sd, "RawInputStream", FakeStream)
    cap = LoopbackCapture(
        config=StreamConfig(role="sender", capture_processing="unprocessed"),
        on_frame=lambda _b, _n: None,
        logger=DummyLogger(),
    )

    with cap._open_unprocessed_stream(
        device_index=27, target_frames=240, callback=lambda *a: None
    ) as stream:
        assert isinstance(stream, FakeStream)

    assert [call["blocksize"] for call in calls] == [240, 0]
    assert all(call["latency"] == 0.02 for call in calls)
