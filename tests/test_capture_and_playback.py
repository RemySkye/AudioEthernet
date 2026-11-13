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
