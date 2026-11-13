from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import psutil

from .config import SUPPORTED_LATENCY_PROFILES, StreamConfig
from .transport_udp import configure_udp_socket

DISCOVER_MESSAGE = "discover"
OFFER_MESSAGE = "offer"
PROTOCOL_TAG = "audioethernet-v1"

_VIRTUAL_IFACE_HINTS = (
    "vethernet",
    "hyper-v",
    "wsl",
    "tailscale",
    "zerotier",
    "wireguard",
    "vmware",
    "virtualbox",
    "vbox",
    "loopback",
    "docker",
)


@dataclass(slots=True)
class SenderOffer:
    sender_ip: str
    sender_name: str
    sample_rate: int
    bit_depth: int
    channels: int
    latency_profile: Optional[str] = None


def _safe_json_parse(payload: bytes) -> Optional[dict]:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _iface_priority(iface_name: str, speed_mbps: int) -> tuple[int, int]:
    lowered = iface_name.lower()
    is_virtual = any(hint in lowered for hint in _VIRTUAL_IFACE_HINTS)
    if is_virtual:
        tier = 2
    elif "wi-fi" in lowered or "wifi" in lowered or "wlan" in lowered:
        tier = 1
    else:
        tier = 0
    return (tier, -speed_mbps)


def _get_broadcast_addresses() -> list[str]:
    candidates: list[tuple[tuple[int, int], str]] = []
    seen: set[str] = set()

    try:
        stats = psutil.net_if_stats()
        addrs_by_if = psutil.net_if_addrs()
    except OSError:
        return ["255.255.255.255"]

    for iface, addr_list in addrs_by_if.items():
        if_stat = stats.get(iface)
        if if_stat is not None and not if_stat.isup:
            continue
        speed = int(getattr(if_stat, "speed", 0) or 0) if if_stat else 0
        prio = _iface_priority(iface, speed)

        for addr in addr_list:
            if addr.family != socket.AF_INET or not addr.address:
                continue
            if addr.address.startswith("127.") or addr.address.startswith("169.254."):
                continue
            if not addr.netmask or addr.netmask == "255.255.255.255":
                continue
            try:
                net = ipaddress.IPv4Interface(f"{addr.address}/{addr.netmask}").network
                bcast = str(net.broadcast_address)
            except ValueError:
                continue
            if bcast not in seen:
                seen.add(bcast)
                candidates.append((prio, bcast))

    candidates.sort(key=lambda item: item[0])
    addresses = [bcast for _, bcast in candidates]
    if "255.255.255.255" not in seen:
        addresses.append("255.255.255.255")
    return addresses


class SenderDiscoveryService:
    def __init__(
        self,
        config: StreamConfig,
        on_receiver_discover: Callable[[str, int], None],
    ) -> None:
        self._config = config
        self._on_receiver_discover = on_receiver_discover
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        configure_udp_socket(self._sock)
        self._sock.bind(("", self._config.control_port))
        self._sock.settimeout(0.5)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="discovery-sender", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue

            message = _safe_json_parse(data)
            if not message:
                continue
            if message.get("tag") != PROTOCOL_TAG:
                continue
            if message.get("type") != DISCOVER_MESSAGE:
                continue

            try:
                receiver_port = int(message.get("data_port", 0))
            except (TypeError, ValueError):
                continue
            if not 1 <= receiver_port <= 65535:
                continue

            receiver_ip = addr[0]
            self._on_receiver_discover(receiver_ip, receiver_port)

            offer = {
                "tag": PROTOCOL_TAG,
                "type": OFFER_MESSAGE,
                "sender_name": self._config.endpoint_name,
                "sample_rate": self._config.sample_rate,
                "bit_depth": self._config.bit_depth,
                "channels": self._config.channels,
                "latency_profile": self._config.latency_profile,
                "ts": time.time(),
            }
            try:
                self._sock.sendto(json.dumps(offer).encode("utf-8"), addr)
            except OSError:
                continue


class ReceiverDiscoveryClient:
    def __init__(self, config: StreamConfig) -> None:
        self._config = config
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        configure_udp_socket(self._sock)
        self._sock.bind(("", 0))
        self._sock.settimeout(0.15)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def _build_discover_payload(self) -> bytes:
        discover = {
            "tag": PROTOCOL_TAG,
            "type": DISCOVER_MESSAGE,
            "receiver_name": self._config.endpoint_name,
            "data_port": self._config.data_port,
            "ts": time.time(),
        }
        return json.dumps(discover).encode("utf-8")

    def send_keepalive(self, sender_ip: str) -> None:
        payload = self._build_discover_payload()
        try:
            self._sock.sendto(payload, (sender_ip, self._config.control_port))
        except OSError:
            pass

    def _recv_matching_offer(self, deadline: float) -> Optional[SenderOffer]:
        while time.monotonic() < deadline:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return None

            message = _safe_json_parse(data)
            if not message:
                continue
            if message.get("tag") != PROTOCOL_TAG:
                continue
            if message.get("type") != OFFER_MESSAGE:
                continue

            sender_name = str(message.get("sender_name", "unknown"))
            if (
                self._config.sender_name
                and self._config.sender_name.lower() not in sender_name.lower()
            ):
                continue

            try:
                sample_rate = int(message.get("sample_rate", 0))
                bit_depth = int(message.get("bit_depth", 0))
                channels = int(message.get("channels", 0))
            except (TypeError, ValueError):
                continue

            raw_profile = message.get("latency_profile")
            latency_profile = (
                str(raw_profile)
                if raw_profile in SUPPORTED_LATENCY_PROFILES
                else None
            )

            return SenderOffer(
                sender_ip=addr[0],
                sender_name=sender_name,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
                latency_profile=latency_profile,
            )

        return None

    def discover_once(self, timeout_seconds: float = 1.0) -> Optional[SenderOffer]:
        payload = self._build_discover_payload()

        if self._config.target_ip:
            targets = [self._config.target_ip]
        else:
            targets = _get_broadcast_addresses()

        overall_deadline = time.monotonic() + timeout_seconds
        per_target_slice = max(0.15, timeout_seconds / max(1, len(targets)))

        for idx, target in enumerate(targets):
            now = time.monotonic()
            if now >= overall_deadline:
                break
            try:
                self._sock.sendto(payload, (target, self._config.control_port))
            except OSError:
                continue

            slice_deadline = min(overall_deadline, now + per_target_slice)
            if idx == len(targets) - 1:
                slice_deadline = overall_deadline
            offer = self._recv_matching_offer(slice_deadline)
            if offer is not None:
                return offer

        return None
