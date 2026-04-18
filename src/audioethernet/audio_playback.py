from __future__ import annotations

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
        self._stream: Optional[sd.RawOutputStream] = None
        self._stream_format = StreamFormat(
            channels=self._config.channels,
            bit_depth=self._config.bit_depth,
            sample_rate=self._config.sample_rate,
            frame_samples=self._config.frame_samples,
        )

    def start(self) -> None:
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
        if stream_format == self._stream_format:
            return

        self._stream_format = stream_format
        if self._stream is None:
            return

        self._stream.stop()
        self._stream.close()
        self._stream = self._open_stream(self._stream_format)
        self._stream.start()
        self._logger.info(
            "Receiver playback reconfigured to %s Hz, %s-bit, frame %s samples",
            self._stream_format.sample_rate,
            self._stream_format.bit_depth,
            self._stream_format.frame_samples,
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _open_stream(self, stream_format: StreamFormat) -> sd.RawOutputStream:
        return sd.RawOutputStream(
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
            padded = frame + bytes(requested - len(frame))
            outdata[:] = padded
            return

        outdata[:] = frame[:requested]
