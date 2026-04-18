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
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                # Keep the receiver usable on platforms that do not support the flag.
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        else:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind(("", bind_port))
        except OSError as exc:
            self._sock.close()
            if getattr(exc, "winerror", None) == 10048:
                raise RuntimeError(
                    f"UDP port {bind_port} is already in use. Stop any other AudioEthernet receiver before starting a new one."
                ) from exc
            raise
        self._local_port = self._sock.getsockname()[1]

    @property
    def local_port(self) -> int:
        return self._local_port

    def recv(self, timeout: float = 0.5) -> Optional[tuple[bytes, tuple[str, int]]]:
        self._sock.settimeout(timeout)
        try:
            data, addr = self._sock.recvfrom(65535)
        except socket.timeout:
            return None
        return data, addr

    def close(self) -> None:
        self._sock.close()
