from __future__ import annotations

import threading
import time

import psutil

from .audio_playback import AudioPlayback
from .config import StreamConfig
from .discovery import ReceiverDiscoveryClient, SenderOffer
from .jitter_buffer import AdaptiveJitterBuffer
from .metrics import RuntimeMetrics
from .protocol import FLAG_AUDIO, FLAG_HEARTBEAT, ProtocolError, unpack_packet
from .transport_udp import UDPReceiver


class ReceiverApp:
    def __init__(self, config: StreamConfig, logger) -> None:
        self._config = config
        self._logger = logger
        self._metrics = RuntimeMetrics()
        self._stop_event = threading.Event()

        self._discovery = ReceiverDiscoveryClient(self._config)
        self._udp_receiver = UDPReceiver(self._config.data_port)

        self._jitter = AdaptiveJitterBuffer(
            frame_bytes=self._config.frame_bytes,
            latency_profile=self._config.latency_profile,
        )

        self._playback = AudioPlayback(
            config=self._config,
            frame_provider=self._next_frame,
            logger=self._logger,
        )

        self._receiver_thread: threading.Thread | None = None
        self._metrics_thread: threading.Thread | None = None

        self._state_lock = threading.Lock()
        self._connected = False
        self._sender_ip: str | None = None
        self._last_packet_time = 0.0

        self._process = psutil.Process()
        self._process.cpu_percent(interval=None)

    def run_forever(self) -> None:
        self._logger.info(
            "Receiver starting with %s-bit %s Hz, frame %s ms",
            self._config.bit_depth,
            self._config.sample_rate,
            self._config.frame_ms,
        )

        self._playback.start()

        last_discovery_refresh = 0.0

        self._receiver_thread = threading.Thread(
            target=self._receive_loop,
            name="receiver-network",
            daemon=True,
        )
        self._receiver_thread.start()

        self._metrics_thread = threading.Thread(
            target=self._metrics_loop,
            name="receiver-metrics",
            daemon=True,
        )
        self._metrics_thread.start()

        try:
            while not self._stop_event.is_set():
                if not self._is_connected():
                    offer = self._discovery.discover_once(
                        timeout_seconds=self._config.reconnect_seconds
                    )
                    if offer and self._offer_matches_local_format(offer):
                        self._set_connected(offer.sender_ip)
                        self._logger.info(
                            "Connected to sender %s (%s)",
                            offer.sender_name,
                            offer.sender_ip,
                        )
                        last_discovery_refresh = time.monotonic()
                else:
                    now = time.monotonic()
                    if now - last_discovery_refresh >= self._config.heartbeat_seconds:
                        # Refresh sender-side peer liveness so stream is not dropped.
                        self._discovery.discover_once(timeout_seconds=0.1)
                        last_discovery_refresh = now

                    if self._stream_timed_out():
                        self._logger.warning("Stream timed out, returning to discovery")
                        self._set_disconnected()
                time.sleep(0.1)
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        self._playback.stop()

        if self._receiver_thread and self._receiver_thread.is_alive():
            self._receiver_thread.join(timeout=2.0)
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=2.0)

        self._discovery.close()
        self._udp_receiver.close()
        self._logger.info("Receiver stopped")

    def _offer_matches_local_format(self, offer: SenderOffer) -> bool:
        if offer.channels != self._config.channels:
            self._logger.warning(
                "Sender channel mismatch. sender=%s local=%s",
                offer.channels,
                self._config.channels,
            )
            return False
        if offer.sample_rate != self._config.sample_rate:
            self._logger.warning(
                "Sender sample rate mismatch. sender=%s local=%s",
                offer.sample_rate,
                self._config.sample_rate,
            )
            return False
        if offer.bit_depth != self._config.bit_depth:
            self._logger.warning(
                "Sender bit depth mismatch. sender=%s local=%s",
                offer.bit_depth,
                self._config.bit_depth,
            )
            return False
        return True

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

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            incoming = self._udp_receiver.recv(timeout=0.5)
            if incoming is None:
                continue

            data, addr = incoming
            try:
                packet = unpack_packet(data)
            except ProtocolError:
                self._metrics.inc_malformed_packets(1)
                continue

            locked_ip = self._sender_locked_ip()
            if locked_ip is not None and addr[0] != locked_ip:
                continue

            self._metrics.inc_packets_received(1)
            self._mark_packet()

            if not self._is_connected():
                self._set_connected(addr[0])
                self._logger.info("Stream detected from sender %s", addr[0])

            if packet.flags & FLAG_HEARTBEAT:
                continue

            if packet.flags & FLAG_AUDIO:
                if packet.sample_rate != self._config.sample_rate:
                    continue
                if packet.bit_depth != self._config.bit_depth:
                    continue
                if packet.channels != self._config.channels:
                    continue

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
