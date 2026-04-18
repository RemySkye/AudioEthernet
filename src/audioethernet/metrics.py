from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(slots=True)
class MetricsSnapshot:
    dropped_frames: int
    underruns: int
    overruns: int
    malformed_packets: int
    packets_received: int
    packets_sent: int


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dropped_frames = 0
        self._underruns = 0
        self._overruns = 0
        self._malformed_packets = 0
        self._packets_received = 0
        self._packets_sent = 0

    def inc_dropped_frames(self, count: int = 1) -> None:
        with self._lock:
            self._dropped_frames += count

    def inc_underruns(self, count: int = 1) -> None:
        with self._lock:
            self._underruns += count

    def inc_overruns(self, count: int = 1) -> None:
        with self._lock:
            self._overruns += count

    def inc_malformed_packets(self, count: int = 1) -> None:
        with self._lock:
            self._malformed_packets += count

    def inc_packets_received(self, count: int = 1) -> None:
        with self._lock:
            self._packets_received += count

    def inc_packets_sent(self, count: int = 1) -> None:
        with self._lock:
            self._packets_sent += count

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                dropped_frames=self._dropped_frames,
                underruns=self._underruns,
                overruns=self._overruns,
                malformed_packets=self._malformed_packets,
                packets_received=self._packets_received,
                packets_sent=self._packets_sent,
            )
