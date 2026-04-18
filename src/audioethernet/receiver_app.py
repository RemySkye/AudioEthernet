from __future__ import annotations

import threading
import time

import psutil

from .audio_playback import AudioPlayback
from .config import StreamConfig
from .firewall import ensure_receiver_firewall_rule
from .discovery import ReceiverDiscoveryClient
from .jitter_buffer import AdaptiveJitterBuffer
from .metrics import RuntimeMetrics
from .protocol import (
    FLAG_AUDIO,
    FLAG_HEARTBEAT,
    ProtocolError,
    StreamFormat,
    unpack_packet,
)


class ReceiverApp:
    def __init__(self, config: StreamConfig, logger) -> None:
        self._config = config
        self._logger = logger
        self._metrics = RuntimeMetrics()
        self._stop_event = threading.Event()

        self._discovery = ReceiverDiscoveryClient(self._config)

        self._stream_format = StreamFormat(
            channels=self._config.channels,
            bit_depth=self._config.bit_depth,
            sample_rate=self._config.sample_rate,
            frame_samples=self._config.frame_samples,
        )

        self._jitter = AdaptiveJitterBuffer(
            frame_bytes=self._stream_format.frame_bytes,
            profile=self._config.profile,
            max_frames=self._config.queue_max_frames,
        )

        self._playback = AudioPlayback(
            config=self._config,
            frame_provider=self._next_frame,
            logger=self._logger,
        )

        self._network_thread: threading.Thread | None = None
        self._metrics_thread: threading.Thread | None = None

        self._state_lock = threading.Lock()
        self._connected = False
        self._sender_ip: str | None = None
        self._last_packet_time = 0.0

        self._process = psutil.Process()
        self._process.cpu_percent(interval=None)

    def run_forever(self) -> None:
        self._logger.info(
            "Receiver starting with %s-bit %s Hz, profile=%s, frame %s ms, port %s",
            self._config.bit_depth,
            self._config.sample_rate,
            self._config.profile,
            self._config.frame_ms,
            self._config.port,
        )

        ensure_receiver_firewall_rule(self._config.port, self._logger)
        self._playback.start()

        self._network_thread = threading.Thread(
            target=self._network_loop,
            name="receiver-network",
            daemon=True,
        )
        self._network_thread.start()

        self._metrics_thread = threading.Thread(
            target=self._metrics_loop,
            name="receiver-metrics",
            daemon=True,
        )
        self._metrics_thread.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(0.1)
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        self._playback.stop()

        if self._network_thread and self._network_thread.is_alive():
            self._network_thread.join(timeout=2.0)
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=2.0)

        self._discovery.close()
        self._logger.info("Receiver stopped")

    def _apply_stream_format(self, stream_format: StreamFormat) -> None:
        with self._state_lock:
            if stream_format == self._stream_format:
                return

            self._stream_format = stream_format
            self._jitter = AdaptiveJitterBuffer(
                frame_bytes=stream_format.frame_bytes,
                profile=self._config.profile,
                max_frames=self._config.queue_max_frames,
            )

        self._playback.set_stream_format(stream_format)
        self._logger.info(
            "Receiver synced to sender stream %s Hz, %s-bit, %s channels, frame %s samples",
            stream_format.sample_rate,
            stream_format.bit_depth,
            stream_format.channels,
            stream_format.frame_samples,
        )

    def _set_connected(self, sender_ip: str) -> None:
        with self._state_lock:
            self._connected = True
            self._sender_ip = sender_ip
            self._last_packet_time = time.monotonic()

    def _set_disconnected(self) -> None:
        with self._state_lock:
            self._connected = False
            self._sender_ip = None
            self._last_packet_time = 0.0
        self._jitter.reset()

    def _is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    def _stream_timed_out(self) -> bool:
        with self._state_lock:
            if not self._connected:
                return False
            return (
                time.monotonic() - self._last_packet_time
            ) > self._config.receiver_stream_timeout_seconds

    def _sender_locked_ip(self) -> str | None:
        with self._state_lock:
            return self._sender_ip

    def _mark_packet(self) -> None:
        with self._state_lock:
            self._last_packet_time = time.monotonic()

    def _network_loop(self) -> None:
        last_discovery_sent = 0.0

        while not self._stop_event.is_set():
            now = time.monotonic()
            discovery_interval = (
                self._config.heartbeat_seconds
                if self._is_connected()
                else self._config.reconnect_seconds
            )
            if (now - last_discovery_sent) >= discovery_interval:
                self._discovery.send_discover()
                last_discovery_sent = now

            incoming = self._discovery.recv(timeout_seconds=0.5)
            if incoming is None:
                if self._stream_timed_out():
                    self._logger.warning("Stream timed out, returning to discovery")
                    self._set_disconnected()
                continue

            data, addr = incoming
            try:
                packet = unpack_packet(data)
            except ProtocolError:
                continue

            locked_ip = self._sender_locked_ip()
            if locked_ip is not None and addr[0] != locked_ip:
                continue

            if not self._is_connected():
                self._set_connected(addr[0])
                self._logger.info("Stream detected from sender %s", addr[0])

            self._metrics.inc_packets_received(1)
            self._mark_packet()

            packet_format = packet.stream_format
            self._apply_stream_format(packet_format)

            if packet.flags & FLAG_HEARTBEAT:
                continue

            if packet.flags & FLAG_AUDIO:
                dropped = self._jitter.push(packet.payload)
                if dropped:
                    self._metrics.inc_dropped_frames(dropped)
                    self._metrics.inc_overruns(dropped)

    def _next_frame(self) -> bytes:
        frame, dropped, underrun = self._jitter.pop()
        if dropped:
            self._metrics.inc_dropped_frames(dropped)
        if underrun:
            self._metrics.inc_underruns(1)
        return frame

    def _metrics_loop(self) -> None:
        while not self._stop_event.wait(10.0):
            snapshot = self._metrics.snapshot()
            cpu = self._process.cpu_percent(interval=None)
            mem_mb = self._process.memory_info().rss / (1024 * 1024)
            depth = self._jitter.depth()
            self._logger.info(
                "receiver metrics | connected=%s sender=%s buffer=%s recv=%s underrun=%s drop=%s cpu=%.1f%% mem=%.1fMB",
                self._is_connected(),
                self._sender_locked_ip(),
                depth,
                snapshot.packets_received,
                snapshot.underruns,
                snapshot.dropped_frames,
                cpu,
                mem_mb,
            )
