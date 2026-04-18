from __future__ import annotations

import ctypes
import threading
import warnings
from typing import Callable

import numpy as np
import soundcard as sc

from .config import StreamConfig

AudioFrameCallback = Callable[[bytes, int], None]

PROCESSED_RECORDER_BLOCK_MULTIPLIER = 2
ENABLE_16BIT_DITHER = True

# SoundCard may emit this warning under transient OS scheduling jitter
# even while capture continues correctly.
warnings.filterwarnings(
    "ignore",
    message="data discontinuity in recording",
)


class LoopbackCapture:
    def __init__(
        self,
        *,
        config: StreamConfig,
        on_frame: AudioFrameCallback,
        logger,
    ) -> None:
        self._config = config
        self._on_frame = on_frame
        self._logger = logger
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._startup_error: Exception | None = None
        self._mode_lock = threading.Lock()
        self._active_processing_mode = self._config.capture_processing

    def active_processing_mode(self) -> str:
        with self._mode_lock:
            return self._active_processing_mode

    def _set_active_processing_mode(self, mode: str) -> None:
        with self._mode_lock:
            previous = self._active_processing_mode
            self._active_processing_mode = mode

        if previous != mode:
            self._logger.info("Capture mode switched: %s -> %s", previous, mode)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._started_event.clear()
        self._startup_error = None

        self._thread = threading.Thread(
            target=self._capture_loop,
            name="capture-loop",
            daemon=True,
        )
        self._thread.start()

        self._started_event.wait(timeout=5.0)
        if self._startup_error is not None:
            raise RuntimeError("Failed to start loopback capture") from self._startup_error

        if not self._started_event.is_set():
            raise RuntimeError("Loopback capture did not initialize in time")

        active_mode = self.active_processing_mode()
        self._logger.info(
            "Sender capture started in %s mode (requested: %s) at %s Hz, %s-bit",
            active_mode,
            self._config.capture_processing,
            self._config.sample_rate,
            self._config.bit_depth,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _capture_loop(self) -> None:
        self._set_active_processing_mode("processed")
        self._capture_loop_processed()

    def _capture_loop_processed(self) -> None:
        self._set_active_processing_mode("processed")
        com_initialized = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            com_initialized = True

            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default speaker available for loopback capture")

            mic = sc.get_microphone(speaker.name, include_loopback=True)
            self._logger.info("Using loopback source: %s", mic)

            target_frames = self._config.frame_samples
            frame_bytes = self._config.frame_bytes
            chunk_frames = target_frames * PROCESSED_RECORDER_BLOCK_MULTIPLIER
            pending_bytes = bytearray()

            with mic.recorder(
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                blocksize=chunk_frames,
            ) as recorder:
                self._started_event.set()

                while not self._stop_event.is_set():
                    block = recorder.record(numframes=chunk_frames)
                    if block is None or block.size == 0:
                        continue

                    pending_bytes.extend(self._float_to_pcm_bytes(block))
                    while len(pending_bytes) >= frame_bytes:
                        frame = bytes(pending_bytes[:frame_bytes])
                        del pending_bytes[:frame_bytes]
                        self._on_frame(frame, target_frames)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._startup_error = exc
            self._started_event.set()
            self._logger.exception("Loopback capture failed: %s", exc)
        finally:
            if com_initialized:
                ctypes.windll.ole32.CoUninitialize()

    def _float_to_pcm_bytes(self, block: np.ndarray) -> bytes:
        clipped = np.clip(block, -1.0, 1.0)
        if self._config.bit_depth == 16:
            if ENABLE_16BIT_DITHER:
                noise = (
                    np.random.random_sample(clipped.shape)
                    - np.random.random_sample(clipped.shape)
                ) / 32768.0
                clipped = np.clip(clipped + noise, -1.0, 1.0)
            pcm = np.rint(clipped * 32767.0).astype(np.int16)
            return pcm.tobytes(order="C")

        pcm = np.rint(clipped * 8388607.0).astype(np.int32)
        return pcm.tobytes(order="C")
