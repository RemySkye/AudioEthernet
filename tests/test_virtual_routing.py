from types import SimpleNamespace

from audioethernet.config import StreamConfig
from audioethernet.virtual_routing import LocalSpeakerMuteGuard


class DummyLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def debug(self, *_args, **_kwargs) -> None:
        pass


def test_virtual_routing_switches_to_cable_input_and_restores(monkeypatch) -> None:
    monkeypatch.setattr("audioethernet.virtual_routing.sys.platform", "win32")

    realtek = SimpleNamespace(id="{orig-id}", name="Speakers (2- Realtek(R) Audio)")
    cable = SimpleNamespace(id="{cable-id}", name="CABLE Input (VB-Audio Virtual Cable)")
    switched: list[str] = []

    guard = LocalSpeakerMuteGuard(
        StreamConfig(role="sender", capture_processing="unprocessed"),
        DummyLogger(),
        all_speakers_fn=lambda: [realtek, cable],
        default_speaker_fn=lambda: realtek,
        set_default_endpoint_fn=switched.append,
    )
    monkeypatch.setattr(guard, "_register_console_ctrl_handler", lambda: None)

    assert guard.activate() is True
    assert switched == ["{cable-id}"]

    # Second restore call must be a safe no-op.
    guard.restore()
    guard.restore()
    assert switched == ["{cable-id}", "{orig-id}"]


def test_virtual_routing_skips_when_keep_local_speakers_or_processed(monkeypatch) -> None:
    monkeypatch.setattr("audioethernet.virtual_routing.sys.platform", "win32")

    realtek = SimpleNamespace(id="{orig-id}", name="Speakers (2- Realtek(R) Audio)")
    cable = SimpleNamespace(id="{cable-id}", name="CABLE Input (VB-Audio Virtual Cable)")
    switched: list[str] = []

    for cfg in (
        StreamConfig(
            role="sender", capture_processing="unprocessed", keep_local_speakers=True
        ),
        StreamConfig(
            role="sender", capture_processing="unprocessed", capture_device="23"
        ),
        StreamConfig(role="sender", capture_processing="processed"),
    ):
        guard = LocalSpeakerMuteGuard(
            cfg,
            DummyLogger(),
            all_speakers_fn=lambda: [realtek, cable],
            default_speaker_fn=lambda: realtek,
            set_default_endpoint_fn=switched.append,
        )
        assert guard.activate() is False
        guard.restore()

    assert switched == []
