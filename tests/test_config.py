from audioethernet.config import StreamConfig


def test_default_audio_format_values() -> None:
    config = StreamConfig(role="sender")
    assert config.profile == "safe"
    assert config.bit_depth == 16
    assert config.sample_rate == 48000
    assert config.channels == 2
    assert config.port == 50482
    assert config.frame_ms == 20
    assert config.frame_samples == 960
    assert config.capture_processing == "processed"
    assert config.queue_max_frames == 512


def test_24_bit_uses_int32_container() -> None:
    config = StreamConfig(role="receiver", bit_depth=24)
    assert config.sounddevice_dtype == "int32"
    assert config.bytes_per_sample == 4


def test_32_bit_uses_int32_container() -> None:
    config = StreamConfig(role="receiver", bit_depth=32)
    assert config.sounddevice_dtype == "int32"
    assert config.bytes_per_sample == 4


def test_44100_hz_uses_whole_samples() -> None:
    config = StreamConfig(role="sender", sample_rate=44100)
    assert config.frame_samples == 882


def test_invalid_sample_rate_raises() -> None:
    config = StreamConfig(role="sender", sample_rate=12345)
    try:
        config.validate()
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_invalid_capture_processing_raises() -> None:
    config = StreamConfig(role="sender", capture_processing="invalid")
    try:
        config.validate()
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_invalid_profile_raises() -> None:
    config = StreamConfig(role="sender", profile="balanced")
    try:
        config.validate()
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_invalid_port_raises() -> None:
    config = StreamConfig(role="sender", port=0)
    try:
        config.validate()
        raised = False
    except ValueError:
        raised = True
    assert raised
