from audioethernet.config import StreamConfig


def test_default_audio_format_values() -> None:
    config = StreamConfig(role="sender")
    assert config.bit_depth == 16
    assert config.sample_rate == 48000
    assert config.channels == 2
    assert config.frame_samples == 240
    assert config.capture_processing == "unprocessed"


def test_24_bit_uses_int32_container() -> None:
    config = StreamConfig(role="receiver", bit_depth=24)
    assert config.sounddevice_dtype == "int32"
    assert config.bytes_per_sample == 4


def test_invalid_sample_rate_raises() -> None:
    config = StreamConfig(role="sender", sample_rate=32000)
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
