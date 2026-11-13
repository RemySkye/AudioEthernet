from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import sounddevice as sd

from .config import StreamConfig
from .protocol import StreamFormat

FrameProvider = Callable[[], bytes]


class AudioPlayback:
    def __init__(
        self,
        *,
        config: StreamConfig,
        frame_provider: FrameProvider,
        logger,
    ) -> None:
        self._config = config
        self._frame_provider = frame_provider
        self._logger = logger
        self._lock = threading.Lock()
        self._stream: Optional[sd.RawOutputStream] = None
        self._stream_format = StreamFormat(
            channels=self._config.channels,
            bit_depth=self._config.bit_depth,
            sample_rate=self._config.sample_rate,
            frame_samples=self._config.frame_samples,
        )
        self._last_status_log = 0.0

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return

            self._stream = self._open_stream(self._stream_format)
            self._stream.start()
            self._logger.info(
                "Receiver playback started at %s Hz, %s-bit, frame %s samples",
                self._stream_format.sample_rate,
                self._stream_format.bit_depth,
                self._stream_format.frame_samples,
            )

    def set_stream_format(self, stream_format: StreamFormat) -> None:
        with self._lock:
            if stream_format == self._stream_format:
                return

            previous_format = self._stream_format
            self._stream_format = stream_format
            if self._stream is None:
                return

            old_stream = self._stream
            self._stream = None
            try:
                old_stream.stop()
                old_stream.close()
            except sd.PortAudioError:
                pass

            try:
                new_stream = self._open_stream(self._stream_format)
                new_stream.start()
                self._stream = new_stream
            except sd.PortAudioError as exc:
                self._logger.error(
                    "Failed to reconfigure playback to %s Hz, %s-bit: %s",
                    stream_format.sample_rate,
                    stream_format.bit_depth,
                    exc,
                )
                self._stream_format = previous_format
                try:
                    fallback = self._open_stream(previous_format)
                    fallback.start()
                    self._stream = fallback
                except sd.PortAudioError:
                    self._stream = None
                return

            self._logger.info(
                "Receiver playback reconfigured to %s Hz, %s-bit, frame %s samples",
                self._stream_format.sample_rate,
                self._stream_format.bit_depth,
                self._stream_format.frame_samples,
            )

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError:
                pass

    def _resolve_output_device(self, channels: int) -> Optional[int]:
        requested = (
            self._config.playback_device.strip()
            if self._config.playback_device
            else None
        )
        if requested is None:
            return None

        devices = sd.query_devices()
        if requested.isdigit():
            idx = int(requested)
            if 0 <= idx < len(devices):
                if int(devices[idx]["max_output_channels"]) >= channels:
                    return idx
                raise RuntimeError(
                    f"Playback device {idx} does not support {channels} output channels"
                )
            raise RuntimeError(f"Playback device index {idx} out of range")

        needle = requested.lower()
        for index, device in enumerate(devices):
            if int(device["max_output_channels"]) < channels:
                continue
            if needle in str(device["name"]).lower():
                return index

        raise RuntimeError(f"Could not find output playback device matching '{requested}'")

    def _open_stream(self, stream_format: StreamFormat) -> sd.RawOutputStream:
        device_index = self._resolve_output_device(stream_format.channels)
        return sd.RawOutputStream(
            device=device_index,
            samplerate=stream_format.sample_rate,
            blocksize=stream_format.frame_samples,
            channels=stream_format.channels,
            dtype=self._dtype_for_bit_depth(stream_format.bit_depth),
            callback=self._audio_callback,
            latency=self._config.profile_settings.playback_latency_seconds,
        )

    @staticmethod
    def _dtype_for_bit_depth(bit_depth: int) -> str:
        if bit_depth == 16:
            return "int16"
        return "int32"

    def _audio_callback(self, outdata, _frames, _time_info, status) -> None:
        if status:
            now = time.monotonic()
            if now - self._last_status_log >= 5.0:
                self._last_status_log = now
                self._logger.warning("Playback status warning: %s", status)

        requested = len(outdata)
        frame = self._frame_provider()
        if not frame:
            outdata[:] = bytes(requested)
            return

        if len(frame) == requested:
            outdata[:] = frame
            return

        if len(frame) < requested:
            outdata[: len(frame)] = frame
            outdata[len(frame) :] = bytes(requested - len(frame))
            return

        outdata[:] = frame[:requested]
