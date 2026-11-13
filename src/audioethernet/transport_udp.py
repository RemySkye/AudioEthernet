from __future__ import annotations

import socket
from typing import Optional

DSCP_EF_TOS = 0xB8


def configure_udp_socket(
    sock: socket.socket,
    *,
    sndbuf: int = 0,
    rcvbuf: int = 0,
    low_latency_tos: bool = False,
) -> None:
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except (OSError, AttributeError):
            pass

    if sndbuf > 0:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
        except OSError:
            pass

    if rcvbuf > 0:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        except OSError:
            pass

    if low_latency_tos:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, DSCP_EF_TOS)
        except OSError:
            pass


class UDPSender:
    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        configure_udp_socket(self._sock, sndbuf=262144, low_latency_tos=True)

    def send(self, payload: bytes, target: tuple[str, int]) -> bool:
        try:
            self._sock.sendto(payload, target)
            return True
        except OSError:
            return False

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class UDPReceiver:
    def __init__(self, bind_port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        configure_udp_socket(self._sock, rcvbuf=524288)
        self._sock.bind(("", bind_port))

    def recv(self, timeout: float = 0.5) -> Optional[tuple[bytes, tuple[str, int]]]:
        try:
            self._sock.settimeout(timeout)
            data, addr = self._sock.recvfrom(65535)
        except (socket.timeout, OSError):
            return None
        return data, addr

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
