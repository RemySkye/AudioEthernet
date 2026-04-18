from __future__ import annotations

from typing import Callable, Optional

import sounddevice as sd

from .config import StreamConfig

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

    def start(self) -> None:
        self._stream = sd.RawOutputStream(
            samplerate=self._config.sample_rate,
            blocksize=self._config.frame_samples,
            channels=self._config.channels,
            dtype=self._config.sounddevice_dtype,
            callback=self._audio_callback,
            latency="high",
        )
        self._stream.start()
        self._logger.info(
            "Receiver playback started at %s Hz, %s-bit",
            self._config.sample_rate,
            self._config.bit_depth,
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

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
