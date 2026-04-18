from __future__ import annotations

import queue
import threading
import time
from typing import Dict, Tuple

import psutil

from .audio_capture import LoopbackCapture
from .config import StreamConfig
from .discovery import SenderDiscoveryService
from .metrics import RuntimeMetrics
from .protocol import pack_audio_packet, pack_heartbeat
from .transport_udp import UDPSender


class SenderApp:
    def __init__(self, config: StreamConfig, logger) -> None:
        self._config = config
        self._logger = logger
        self._metrics = RuntimeMetrics()
        self._stop_event = threading.Event()

        self._audio_queue: queue.Queue[tuple[bytes, int]] = queue.Queue(
            maxsize=self._config.queue_max_frames
        )

        self._targets: Dict[Tuple[str, int], float] = {}
        self._targets_lock = threading.Lock()

        self._sender = UDPSender()
        self._capture = LoopbackCapture(
            config=self._config,
            on_frame=self._on_audio_frame,
            logger=self._logger,
        )
        self._discovery = SenderDiscoveryService(
            self._config,
            on_receiver_discover=self._on_receiver_discover,
        )

        self._send_thread: threading.Thread | None = None
        self._metrics_thread: threading.Thread | None = None

        self._sequence = 0
        self._timestamp_samples = 0

        self._process = psutil.Process()
        self._process.cpu_percent(interval=None)

    def run_forever(self) -> None:
        self._logger.info(
            "Sender starting with %s-bit %s Hz, frame %s ms, capture=%s",
            self._config.bit_depth,
            self._config.sample_rate,
            self._config.frame_ms,
            self._config.capture_processing,
        )

        self._discovery.start()
        self._capture.start()

        self._send_thread = threading.Thread(
            target=self._send_loop,
            name="sender-network",
            daemon=True,
        )
        self._send_thread.start()

        self._metrics_thread = threading.Thread(
            target=self._metrics_loop,
            name="sender-metrics",
            daemon=True,
        )
        self._metrics_thread.start()

        try:
            while not self._stop_event.wait(0.25):
                pass
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        self._capture.stop()
        self._discovery.stop()

        if self._send_thread and self._send_thread.is_alive():
            self._send_thread.join(timeout=2.0)
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=2.0)

        self._sender.close()
        self._logger.info("Sender stopped")

    def _on_receiver_discover(self, receiver_ip: str, receiver_port: int) -> None:
        key = (receiver_ip, receiver_port)
        with self._targets_lock:
            first_seen = key not in self._targets
            self._targets[key] = time.monotonic()

        if first_seen:
            self._logger.info(
                "Receiver discovered at %s:%s", receiver_ip, receiver_port
            )

    def _on_audio_frame(self, frame_bytes: bytes, frame_samples: int) -> None:
        item = (frame_bytes, frame_samples)
        try:
            self._audio_queue.put_nowait(item)
            return
        except queue.Full:
            self._metrics.inc_dropped_frames(1)

        try:
            self._audio_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            self._audio_queue.put_nowait(item)
        except queue.Full:
            self._metrics.inc_dropped_frames(1)

    def _active_targets(self) -> list[tuple[str, int]]:
        now = time.monotonic()
        timeout = self._config.sender_peer_timeout_seconds
        active: list[tuple[str, int]] = []
        stale: list[tuple[str, int]] = []

        with self._targets_lock:
            for target, last_seen in self._targets.items():
                if (now - last_seen) <= timeout:
                    active.append(target)
                else:
                    stale.append(target)
            for target in stale:
                del self._targets[target]

        return active

    def _send_loop(self) -> None:
        last_heartbeat = 0.0

        while not self._stop_event.is_set():
            active_targets = self._active_targets()

            try:
                frame_bytes, frame_samples = self._audio_queue.get(timeout=0.05)
            except queue.Empty:
                now = time.monotonic()
                if (
                    active_targets
                    and (now - last_heartbeat) >= self._config.heartbeat_seconds
                ):
                    heartbeat = pack_heartbeat(
                        channels=self._config.channels,
                        bit_depth=self._config.bit_depth,
                        sample_rate=self._config.sample_rate,
                        frame_samples=self._config.frame_samples,
                        sequence=self._sequence,
                        timestamp_samples=self._timestamp_samples,
                    )
                    for target in active_targets:
                        self._sender.send(heartbeat, target)
                        self._metrics.inc_packets_sent(1)
                    self._sequence = (self._sequence + 1) & 0xFFFFFFFF
                    last_heartbeat = now
                continue

            packet = pack_audio_packet(
                channels=self._config.channels,
                bit_depth=self._config.bit_depth,
                sample_rate=self._config.sample_rate,
                frame_samples=frame_samples,
                sequence=self._sequence,
                timestamp_samples=self._timestamp_samples,
                payload=frame_bytes,
            )

            for target in active_targets:
                self._sender.send(packet, target)
                self._metrics.inc_packets_sent(1)

            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            self._timestamp_samples += frame_samples

    def _metrics_loop(self) -> None:
        while not self._stop_event.wait(10.0):
            snapshot = self._metrics.snapshot()
            cpu = self._process.cpu_percent(interval=None)
            mem_mb = self._process.memory_info().rss / (1024 * 1024)
            targets = len(self._active_targets())
            queue_depth = self._audio_queue.qsize()
            self._logger.info(
                "sender metrics | targets=%s queue=%s sent=%s drop=%s cpu=%.1f%% mem=%.1fMB",
                targets,
                queue_depth,
                snapshot.packets_sent,
                snapshot.dropped_frames,
                cpu,
                mem_mb,
            )
