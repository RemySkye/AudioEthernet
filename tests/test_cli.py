from audioethernet.cli import build_parser


def test_sender_defaults_to_balanced_profile_and_unprocessed_capture() -> None:
    parser = build_parser()
    args = parser.parse_args(["-s"])

    assert args.latency_profile == "balanced"
    assert args.capture_processing == "unprocessed"
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
    assert "unmuted" in help_text