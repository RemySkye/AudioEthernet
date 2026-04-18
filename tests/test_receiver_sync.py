from audioethernet.config import StreamConfig
from audioethernet.protocol import StreamFormat
import audioethernet.receiver_app as receiver_app


class DummyLogger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


class FakeDiscovery:
    def __init__(self, config) -> None:
        self.config = config
        self.local_port = 62000

    def close(self) -> None:
        pass

    def discover_once(self, timeout_seconds: float = 1.0):
        return None

    def send_discover(self) -> None:
        pass

    def recv(self, timeout_seconds: float = 0.5):
        return None

    def close(self) -> None:
        pass


class FakePlayback:
    instances: list["FakePlayback"] = []

    def __init__(self, *, config, frame_provider, logger) -> None:
        self.config = config
        self.frame_provider = frame_provider
        self.logger = logger
        self.started = False
        self.stopped = False
        self.formats: list[StreamFormat] = []
        FakePlayback.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def set_stream_format(self, stream_format: StreamFormat) -> None:
        self.formats.append(stream_format)


class FakeJitterBuffer:
    instances: list["FakeJitterBuffer"] = []

    def __init__(self, *, frame_bytes: int, profile: str, max_frames: int) -> None:
        self.frame_bytes = frame_bytes
        self.profile = profile
        self.max_frames = max_frames
        self.reset_calls = 0
        FakeJitterBuffer.instances.append(self)

    def reset(self) -> None:
        self.reset_calls += 1

    def push(self, frame: bytes) -> int:
        return 0

    def pop(self) -> tuple[bytes, int, bool]:
        return b"", 0, True

    def depth(self) -> int:
        return 0


def test_receiver_applies_sender_stream_format(monkeypatch) -> None:
    FakePlayback.instances.clear()
    FakeJitterBuffer.instances.clear()

    monkeypatch.setattr(receiver_app, "ReceiverDiscoveryClient", FakeDiscovery)
    monkeypatch.setattr(receiver_app, "AudioPlayback", FakePlayback)
    monkeypatch.setattr(receiver_app, "AdaptiveJitterBuffer", FakeJitterBuffer)

    config = StreamConfig(role="receiver")
    app = receiver_app.ReceiverApp(config, DummyLogger())

    initial_buffer_count = len(FakeJitterBuffer.instances)
    sender_format = StreamFormat(
        channels=2,
        bit_depth=24,
        sample_rate=96000,
        frame_samples=960,
    )

    app._apply_stream_format(sender_format)

    assert app._stream_format == sender_format
    assert len(FakeJitterBuffer.instances) == initial_buffer_count + 1
    assert FakeJitterBuffer.instances[-1].frame_bytes == sender_format.frame_bytes
    assert FakeJitterBuffer.instances[-1].profile == "safe"
    assert FakePlayback.instances[-1].formats == [sender_format]

    app._apply_stream_format(sender_format)
    assert len(FakeJitterBuffer.instances) == initial_buffer_count + 1
    assert FakePlayback.instances[-1].formats == [sender_format]
