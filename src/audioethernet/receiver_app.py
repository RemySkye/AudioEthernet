from __future__ import annotations

import threading
import time

import psutil

from .audio_playback import AudioPlayback
from .config import (
    SUPPORTED_BIT_DEPTHS,
    SUPPORTED_FRAME_MS,
    SUPPORTED_SAMPLE_RATES,
    StreamConfig,
)
from .discovery import ReceiverDiscoveryClient, SenderOffer
from .jitter_buffer import AdaptiveJitterBuffer
from .metrics import RuntimeMetrics
from .protocol import FLAG_AUDIO, FLAG_HEARTBEAT, Packet, ProtocolError, unpack_packet
from .transport_udp import UDPReceiver


def _frame_ms_from_samples(sample_rate: int, frame_samples: int) -> int | None:
    if sample_rate <= 0 or frame_samples <= 0:
        return None

    frame_ms_float = (frame_samples * 1000.0) / sample_rate
    rounded = int(round(frame_ms_float))

    if rounded not in SUPPORTED_FRAME_MS:
        return None
    if abs(frame_ms_float - rounded) > 0.15:
        return None
    return rounded


def _stream_label(bit_depth: int, sample_rate: int, channels: int, frame_ms: int) -> str:
    return f"{bit_depth}-bit/{sample_rate}Hz/{channels}ch/{frame_ms}ms"


class ReceiverApp:
    def __init__(self, config: StreamConfig, logger) -> None:
        self._config = config
        self._logger = logger
        self._metrics = RuntimeMetrics()
        self._stop_event = threading.Event()
        self._pipeline_lock = threading.Lock()

        self._discovery = ReceiverDiscoveryClient(self._config)
        self._udp_receiver = UDPReceiver(self._config.data_port)

        self._last_rejected_stream: tuple[int, int, int, int] | None = None

        self._build_pipeline()

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
            "Receiver starting (auto-sync enabled). Initial stream preference: %s",
            self._current_stream_label(),
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
                    if offer and self._sync_stream_from_offer(offer):
                        changed = self._set_sender_candidate(offer.sender_ip)
                        if changed:
                            self._logger.info(
                                "Sender discovered %s (%s) | stream=%s | waiting for packets",
                                offer.sender_name,
                                offer.sender_ip,
                                self._current_stream_label(),
                            )
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

        with self._pipeline_lock:
            self._playback.stop()

        if self._receiver_thread and self._receiver_thread.is_alive():
            self._receiver_thread.join(timeout=2.0)
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=2.0)

        self._discovery.close()
        self._udp_receiver.close()
        self._logger.info("Receiver stopped")

    def _build_pipeline(self) -> None:
        self._jitter = AdaptiveJitterBuffer(
            frame_bytes=self._config.frame_bytes,
            latency_profile=self._config.latency_profile,
        )
        self._playback = AudioPlayback(
            config=self._config,
            frame_provider=self._next_frame,
            logger=self._logger,
        )

    def _current_stream_label(self) -> str:
        return _stream_label(
            bit_depth=self._config.bit_depth,
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            frame_ms=self._config.frame_ms,
        )

    def _sync_stream_from_offer(self, offer: SenderOffer) -> bool:
        return self._sync_stream(
            sender_ip=offer.sender_ip,
            sample_rate=offer.sample_rate,
            bit_depth=offer.bit_depth,
            channels=offer.channels,
            frame_samples=offer.frame_samples,
            source=f"discovery offer from {offer.sender_name}",
        )

    def _sync_stream_from_packet(self, packet: Packet, sender_ip: str) -> bool:
        return self._sync_stream(
            sender_ip=sender_ip,
            sample_rate=packet.sample_rate,
            bit_depth=packet.bit_depth,
            channels=packet.channels,
            frame_samples=packet.frame_samples,
            source="packet header",
        )

    def _sync_stream(
        self,
        *,
        sender_ip: str,
        sample_rate: int,
        bit_depth: int,
        channels: int,
        frame_samples: int,
        source: str,
    ) -> bool:
        frame_ms = self._config.frame_ms
        if frame_samples > 0:
            inferred_frame_ms = _frame_ms_from_samples(sample_rate, frame_samples)
            if inferred_frame_ms is None:
                self._warn_stream_rejected_once(
                    sender_ip=sender_ip,
                    sample_rate=sample_rate,
                    bit_depth=bit_depth,
                    channels=channels,
                    frame_samples=frame_samples,
                    reason=(
                        f"unsupported frame size ({frame_samples} samples at {sample_rate} Hz)"
                    ),
                )
                return False
            frame_ms = inferred_frame_ms

        if channels != 2:
            self._warn_stream_rejected_once(
                sender_ip=sender_ip,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
                frame_samples=frame_samples,
                reason=f"unsupported channels ({channels})",
            )
            return False
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            self._warn_stream_rejected_once(
                sender_ip=sender_ip,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
                frame_samples=frame_samples,
                reason=f"unsupported sample rate ({sample_rate})",
            )
            return False
        if bit_depth not in SUPPORTED_BIT_DEPTHS:
            self._warn_stream_rejected_once(
                sender_ip=sender_ip,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
                frame_samples=frame_samples,
                reason=f"unsupported bit depth ({bit_depth})",
            )
            return False

        self._last_rejected_stream = None

        current = (
            self._config.sample_rate,
            self._config.bit_depth,
            self._config.channels,
            self._config.frame_ms,
        )
        desired = (sample_rate, bit_depth, channels, frame_ms)
        if current == desired:
            return True

        previous_label = self._current_stream_label()

        try:
            with self._pipeline_lock:
                self._playback.stop()

                self._config.sample_rate = sample_rate
                self._config.bit_depth = bit_depth
                self._config.channels = channels
                self._config.frame_ms = frame_ms

                self._build_pipeline()
                self._playback.start()
        except Exception:
            self._logger.exception(
                "Failed to apply sender stream from %s (%s)", sender_ip, source
            )
            return False

        self._logger.info(
            "Receiver stream synced from %s via %s: %s -> %s",
            sender_ip,
            source,
            previous_label,
            self._current_stream_label(),
        )
        return True

    def _warn_stream_rejected_once(
        self,
        *,
        sender_ip: str,
        sample_rate: int,
        bit_depth: int,
        channels: int,
        frame_samples: int,
        reason: str,
    ) -> None:
        signature = (sample_rate, bit_depth, channels, frame_samples)
        if self._last_rejected_stream == signature:
            return
        self._last_rejected_stream = signature
        self._logger.warning(
            "Rejected stream from %s: %s | stream=%s/%sHz/%sch/%ssamples",
            sender_ip,
            reason,
            bit_depth,
            sample_rate,
            channels,
            frame_samples,
        )

    def _set_connected(self, sender_ip: str) -> None:
        with self._state_lock:
            self._connected = True
            self._sender_ip = sender_ip
            self._last_packet_time = time.monotonic()

    def _set_sender_candidate(self, sender_ip: str) -> bool:
        with self._state_lock:
            changed = self._sender_ip != sender_ip or self._connected
            self._connected = False
            self._sender_ip = sender_ip
            self._last_packet_time = 0.0
            return changed

    def _set_disconnected(self) -> None:
        with self._state_lock:
            self._connected = False
            self._sender_ip = None
            self._last_packet_time = 0.0
        with self._pipeline_lock:
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

            if not self._sync_stream_from_packet(packet, addr[0]):
                continue

            if not self._is_connected():
                self._set_connected(addr[0])
                self._logger.info(
                    "Stream detected from sender %s | stream=%s",
                    addr[0],
                    self._current_stream_label(),
                )

            if packet.flags & FLAG_HEARTBEAT:
                continue

            if packet.flags & FLAG_AUDIO:
                with self._pipeline_lock:
                    dropped = self._jitter.push(packet.payload)
                if dropped:
                    self._metrics.inc_dropped_frames(dropped)
                    self._metrics.inc_overruns(dropped)

    def _next_frame(self) -> bytes:
        with self._pipeline_lock:
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
            with self._pipeline_lock:
                depth = self._jitter.depth()
            connected = self._is_connected()
            sender_ip = self._sender_locked_ip()
            state = "connected" if connected else "searching"
            self._logger.info(
                "receiver stats | state=%s sender=%s stream=%s buffer=%s recv=%s malformed=%s underrun=%s drop=%s cpu=%.1f%% mem=%.1fMB",
                state,
                sender_ip,
                self._current_stream_label(),
                depth,
                snapshot.packets_received,
                snapshot.malformed_packets,
                snapshot.underruns,
                snapshot.dropped_frames,
                cpu,
                mem_mb,
            )
