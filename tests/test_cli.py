from audioethernet.cli import build_parser


def test_sender_defaults_to_safe_profile_and_processed_capture() -> None:
    parser = build_parser()
    args = parser.parse_args(["-s"])

    assert args.profile == "safe"
    assert args.capture_processing == "processed"
    assert args.bit_depth == 16
    assert args.sample_rate == 48000
    assert args.frame_ms is None


def test_help_mentions_profile_and_unprocessed_mode() -> None:
    help_text = build_parser().format_help()

    assert "--profile" in help_text
    assert "-p" in help_text
    assert "--latency-profile" not in help_text
    assert "Stereo Mix" in help_text
    assert "unmuted" in help_text
