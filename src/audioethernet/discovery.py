from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .config import StreamConfig
from .protocol import StreamFormat

DISCOVER_MESSAGE = "discover"
OFFER_MESSAGE = "offer"
PROTOCOL_TAG = "audioethernet-v1"


@dataclass(slots=True)
class SenderOffer:
    sender_ip: str
    sender_name: str
    sample_rate: int
    bit_depth: int
    channels: int
    frame_samples: int

    @property
    def stream_format(self) -> StreamFormat:
        return StreamFormat(
            channels=self.channels,
            bit_depth=self.bit_depth,
            sample_rate=self.sample_rate,
            frame_samples=self.frame_samples,
        )


def _safe_json_parse(payload: bytes) -> Optional[dict]:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


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
        self._send_lock = threading.Lock()

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self._config.port))
        self._sock.settimeout(0.5)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="discovery-sender", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._sock.close()

    def send(self, payload: bytes, target: tuple[str, int]) -> None:
        with self._send_lock:
            try:
                self._sock.sendto(payload, target)
            except ConnectionResetError:
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) == 10054:
                    return
                raise

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue
            except OSError as exc:
                if getattr(exc, "winerror", None) == 10054:
                    continue
                raise

            message = _safe_json_parse(data)
            if not message:
                continue
            if message.get("tag") != PROTOCOL_TAG:
                continue
            if message.get("type") != DISCOVER_MESSAGE:
                continue

            receiver_port = int(message.get("port", 0))
            if not receiver_port:
                receiver_port = int(message.get("data_port", 0))
            if not receiver_port:
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
                "frame_samples": self._config.frame_samples,
                "port": self._config.port,
                "ts": time.time(),
            }
            self._sock.sendto(json.dumps(offer).encode("utf-8"), addr)


class ReceiverDiscoveryClient:
    def __init__(self, config: StreamConfig) -> None:
        self._config = config
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self._config.port))
        self._sock.settimeout(0.4)

    def close(self) -> None:
        self._sock.close()

    def send_discover(self) -> None:
        discover = {
            "tag": PROTOCOL_TAG,
            "type": DISCOVER_MESSAGE,
            "receiver_name": self._config.endpoint_name,
            "port": self._config.port,
            "ts": time.time(),
        }
        self._sock.sendto(
            json.dumps(discover).encode("utf-8"),
            ("255.255.255.255", self._config.port),
        )

    def recv(self, timeout_seconds: float = 0.5) -> Optional[tuple[bytes, tuple[str, int]]]:
        self._sock.settimeout(timeout_seconds)
        try:
            return self._sock.recvfrom(65535)
        except socket.timeout:
            return None
        except ConnectionResetError:
            return None
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10054:
                return None
            raise

    def discover_once(self, timeout_seconds: float = 1.0) -> Optional[SenderOffer]:
        self.send_discover()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            incoming = self.recv(timeout_seconds=timeout_seconds)
            if incoming is None:
                continue
            data, addr = incoming

            message = _safe_json_parse(data)
            if not message:
                continue
            if message.get("tag") != PROTOCOL_TAG:
                continue
            if message.get("type") != OFFER_MESSAGE:
                continue

            return SenderOffer(
                sender_ip=addr[0],
                sender_name=str(message.get("sender_name", "unknown")),
                sample_rate=int(message.get("sample_rate", self._config.sample_rate)),
                bit_depth=int(message.get("bit_depth", self._config.bit_depth)),
                channels=int(message.get("channels", self._config.channels)),
                frame_samples=int(message.get("frame_samples", self._config.frame_samples)),
            )

        return None
