from __future__ import annotations

import ctypes
import subprocess
import sys


def _is_windows_admin() -> bool:
    if sys.platform != "win32":
        return False

    shell32 = getattr(getattr(ctypes, "windll", None), "shell32", None)
    if shell32 is None:
        return False

    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def ensure_receiver_firewall_rule(port: int, logger) -> bool:
    if sys.platform != "win32":
        logger.debug("Skipping firewall rule setup on non-Windows platform")
        return False

    if not _is_windows_admin():
        logger.warning(
            "Cannot create a Windows Firewall rule for UDP port %s without administrator privileges.",
            port,
        )
        return False

    rule_name = f"AudioEthernet-Receiver-UDP-{port}"
    display_name = f"AudioEthernet receiver UDP {port}"
    script = f"""
$ruleName = "{rule_name}"
$displayName = "{display_name}"
$port = {port}
Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -Name $ruleName -DisplayName $displayName -Direction Inbound -Action Allow -Protocol UDP -LocalPort $port -Profile Any | Out-Null
""".strip()

    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.warning(
            "Unable to launch PowerShell to configure Windows Firewall for UDP port %s: %s",
            port,
            exc,
        )
        return False

    if completed.returncode != 0:
        error_text = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        logger.warning(
            "Unable to create Windows Firewall rule for UDP port %s: %s",
            port,
            error_text,
        )
        return False

    logger.info("Allowed inbound UDP port %s in Windows Firewall", port)
    return True
