from types import SimpleNamespace

import audioethernet.firewall as firewall


class DummyLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.debug_messages: list[str] = []

    def info(self, message, *args) -> None:
        self.info_messages.append(message % args if args else message)

    def warning(self, message, *args) -> None:
        self.warning_messages.append(message % args if args else message)

    def debug(self, message, *args) -> None:
        self.debug_messages.append(message % args if args else message)


def test_firewall_helper_creates_rule_on_windows(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, capture_output, text, check):
        captured["command"] = command
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall.sys, "platform", "win32")
    monkeypatch.setattr(firewall, "_is_windows_admin", lambda: True)
    monkeypatch.setattr(firewall.subprocess, "run", fake_run)

    logger = DummyLogger()
    result = firewall.ensure_receiver_firewall_rule(50482, logger)

    assert result is True
    assert captured["command"][5].startswith('$ruleName = "AudioEthernet-Receiver-UDP-50482"')
    assert "New-NetFirewallRule" in captured["command"][5]
    assert logger.info_messages[-1] == "Allowed inbound UDP port 50482 in Windows Firewall"


def test_firewall_helper_warns_without_admin(monkeypatch) -> None:
    monkeypatch.setattr(firewall.sys, "platform", "win32")
    monkeypatch.setattr(firewall, "_is_windows_admin", lambda: False)

    logger = DummyLogger()
    result = firewall.ensure_receiver_firewall_rule(50482, logger)

    assert result is False
    assert any("administrator privileges" in message for message in logger.warning_messages)
