from audioethernet.cli import build_parser


def test_sender_defaults_to_balanced_profile_and_processed_capture() -> None:
    parser = build_parser()
    args = parser.parse_args(["-s"])

    assert args.latency_profile == "balanced"
    assert args.capture_processing == "processed"
    assert args.bit_depth == 16
    assert args.sample_rate == 48000
    assert args.frame_ms == 5
    assert args.control_port == 50481
    assert args.data_port == 50482


def test_help_mentions_latency_profile_and_unprocessed_mode() -> None:
    help_text = build_parser().format_help()

    assert "--latency-profile" in help_text
    assert "--control-port" in help_text
    assert "--data-port" in help_text
    assert "--profile" not in help_text
    assert "--port" not in help_text
    assert "Stereo Mix" in help_text
    assert "processed" in help_text
    assert "--target" in help_text
    assert "--capture-device" in help_text
    assert "--keep-local-speakers" in help_text


def test_latency_profile_controls_unspecified_defaults(monkeypatch) -> None:
    import audioethernet.cli as cli

    captured: dict[str, object] = {}

    class FakeSenderApp:
        def __init__(self, config, logger) -> None:
            captured["config"] = config

        def run_forever(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(cli, "SenderApp", FakeSenderApp)
    cli.main(["-s", "--latency-profile", "stable", "--capture-processing", "unprocessed"])
    cfg = captured["config"]
    assert cfg.frame_ms == 10
    assert cfg.queue_max_frames == 512
    assert cfg.sender_peer_timeout_seconds == 12.0
    assert cfg.capture_processing == "unprocessed"
    assert cfg.keep_local_speakers is False

    cli.main(
        [
            "-s",
            "--capture-processing",
            "unprocessed",
            "--keep-local-speakers",
        ]
    )
    assert captured["config"].keep_local_speakers is True