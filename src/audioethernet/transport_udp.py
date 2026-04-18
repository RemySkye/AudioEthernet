from __future__ import annotations

import socket
from typing import Optional


class UDPSender:
    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, payload: bytes, target: tuple[str, int]) -> None:
        self._sock.sendto(payload, target)

    def close(self) -> None:
        self._sock.close()


class UDPReceiver:
    def __init__(self, bind_port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", bind_port))

    def recv(self, timeout: float = 0.5) -> Optional[tuple[bytes, tuple[str, int]]]:
        self._sock.settimeout(timeout)
        try:
            data, addr = self._sock.recvfrom(65535)
        except socket.timeout:
            return None
        return data, addr

    def close(self) -> None:
        self._sock.close()