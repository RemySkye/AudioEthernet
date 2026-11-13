from __future__ import annotations

import queue
import threading
import time
from typing import Dict, Optional, Tuple

import psutil

from .audio_capture import LoopbackCapture
from .config import StreamConfig
from .discovery import SenderDiscoveryService, peer_interface_priority
from .firewall import ensure_sender_firewall_rule
from .metrics import RuntimeMetrics
from .protocol import pack_audio_packet, pack_heartbeat
from .transport_udp import UDPSender
from .virtual_routing import LocalSpeakerMuteGuard

MAX_UDP_AUDIO_PAYLOAD = 1400


def compute_chunk_samples(frame_samples: int, bytes_per_frame_sample: int) -> int:
    if frame_samples <= 0 or bytes_per_frame_sample <= 0:
        return frame_samples
    max_samples = max(1, MAX_UDP_AUDIO_PAYLOAD // bytes_per_frame_sample)
    if frame_samples <= max_samples:
        return frame_samples

    min_chunks = (frame_samples + max_samples - 1) // max_samples
    for chunks in range(min_chunks, frame_samples + 1):
        if frame_samples % chunks == 0:
            return frame_samples // chunks
    return max_samples


class SenderApp:
    def __init__(self, config: StreamConfig, logger) -> None:
        self._config = config
        self._logger = logger
        self._metrics = RuntimeMetrics()
        self._stop_event = threading.Event()
        self._cleanup_done = False
        self._cleanup_lock = threading.Lock()
        self._fatal_error: Exception | None = None

        assert self._config.queue_max_frames is not None
        self._audio_queue: queue.Queue[tuple[bytes, int]] = queue.Queue(
            maxsize=self._config.queue_max_frames
        )

        self._targets: Dict[Tuple[str, int], float] = {}
        self._receiver_ids: Dict[str, Tuple[str, int]] = {}
        self._targets_lock = threading.Lock()

        self._bytes_per_frame_sample = (
            self._config.channels * self._config.bytes_per_sample
        )
        self._chunk_samples = compute_chunk_samples(
            self._config.frame_samples, self._bytes_per_frame_sample
        )

        self._sender = UDPSender()
        self._capture = LoopbackCapture(
            config=self._config,
            on_frame=self._on_audio_frame,
            logger=self._logger,
            on_error=self._on_capture_error,
        )
        self._discovery = SenderDiscoveryService(
            self._config,
            on_receiver_discover=self._on_receiver_discover,
        )
        self._virtual_routing = LocalSpeakerMuteGuard(self._config, self._logger)

        self._send_thread: threading.Thread | None = None
        self._metrics_thread: threading.Thread | None = None

        self._sequence = 0
        self._timestamp_samples = 0

        self._process = psutil.Process()
        self._process.cpu_percent(interval=None)

    def run_forever(self) -> None:
        self._logger.info(
            "Sender starting with %s-bit %s Hz, frame %s ms (packet %s samples), capture=%s, profile=%s",
            self._config.bit_depth,
            self._config.sample_rate,
            self._config.frame_ms,
            self._chunk_samples,
            self._config.capture_processing,
            self._config.latency_profile,
        )

        ensure_sender_firewall_rule(self._config.control_port, self._logger)
        try:
            self._virtual_routing.activate()
            self._capture.start()
            self._discovery.start()

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

            while not self._stop_event.wait(0.25):
                if self._fatal_error is not None:
                    raise RuntimeError("Sender capture failed") from self._fatal_error
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop_event.set()
        with self._cleanup_lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True

        self._capture.stop()
        self._virtual_routing.restore()
        self._discovery.stop()

        if self._send_thread and self._send_thread.is_alive():
            self._send_thread.join(timeout=2.0)
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=2.0)

        self._sender.close()
        self._logger.info("Sender stopped")

    def _on_capture_error(self, exc: Exception) -> None:
        self._fatal_error = exc
        self._stop_event.set()

    def _on_receiver_discover(
        self,
        receiver_ip: str,
        receiver_port: int,
        receiver_id: Optional[str] = None,
    ) -> None:
        key = (receiver_ip, receiver_port)
        now = time.monotonic()
        with self._targets_lock:
            if receiver_id:
                existing = self._receiver_ids.get(receiver_id)
                if (
                    existing is not None
                    and existing != key
                    and existing in self._targets
                ):
                    new_prio = peer_interface_priority(receiver_ip)
                    old_prio = peer_interface_priority(existing[0])
                    if new_prio >= old_prio:
                        # Keep the higher-priority NIC path for this receiver instance while active.
                        return
                    self._targets.pop(existing, None)
                    self._logger.info(
                        "Upgrading receiver target path from %s:%s to %s:%s",
                        existing[0],
                        existing[1],
                        receiver_ip,
                        receiver_port,
                    )
                self._receiver_ids[receiver_id] = key

            first_seen = key not in self._targets
            self._targets[key] = now

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
        assert self._config.sender_peer_timeout_seconds is not None
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
            if stale:
                stale_set = set(stale)
                for rid in [
                    rid
                    for rid, tgt in self._receiver_ids.items()
                    if tgt in stale_set
                ]:
                    del self._receiver_ids[rid]

        for target in stale:
            self._logger.info("Receiver %s:%s timed out", target[0], target[1])

        return active

    def _send_loop(self) -> None:
        last_heartbeat = 0.0
        assert self._config.heartbeat_seconds is not None

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
                        frame_samples=self._chunk_samples,
                        sequence=self._sequence,
                        timestamp_samples=self._timestamp_samples,
                        latency_profile=self._config.latency_profile,
                    )
                    for target in active_targets:
                        if self._sender.send(heartbeat, target):
                            self._metrics.inc_packets_sent(1)
                    self._sequence = (self._sequence + 1) & 0xFFFFFFFF
                    last_heartbeat = now
                continue

            chunk_samples = compute_chunk_samples(
                frame_samples, self._bytes_per_frame_sample
            )
            offset_samples = 0
            while offset_samples < frame_samples:
                part_samples = min(chunk_samples, frame_samples - offset_samples)
                start_byte = offset_samples * self._bytes_per_frame_sample
                end_byte = start_byte + (part_samples * self._bytes_per_frame_sample)
                chunk_bytes = frame_bytes[start_byte:end_byte]

                if active_targets:
                    packet = pack_audio_packet(
                        channels=self._config.channels,
                        bit_depth=self._config.bit_depth,
                        sample_rate=self._config.sample_rate,
                        frame_samples=part_samples,
                        sequence=self._sequence,
                        timestamp_samples=self._timestamp_samples,
                        payload=chunk_bytes,
                        latency_profile=self._config.latency_profile,
                    )
                    for target in active_targets:
                        if self._sender.send(packet, target):
                            self._metrics.inc_packets_sent(1)

                self._sequence = (self._sequence + 1) & 0xFFFFFFFF
                self._timestamp_samples = (
                    self._timestamp_samples + part_samples
                ) & 0xFFFFFFFFFFFFFFFF
                offset_samples += part_samples

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
