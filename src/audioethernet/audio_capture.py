from __future__ import annotations

import ctypes
import re
import threading
from typing import Callable

import numpy as np
import soundcard as sc
import sounddevice as sd

from .config import StreamConfig

AudioFrameCallback = Callable[[bytes, int], None]

UNPROCESSED_STARTUP_SILENCE_SECONDS = 3.0
UNPROCESSED_SILENCE_INT16_THRESHOLD = 64
UNPROCESSED_SILENCE_INT32_THRESHOLD = 4096

UNPROCESSED_NAME_HINTS = (
    "stereo mix",
    "wave out mix",
    "what u hear",
    "monitor",
)

GENERIC_DEVICE_TOKENS = {
    "audio",
    "device",
    "speakers",
    "speaker",
    "output",
    "input",
    "microphone",
    "realtek",
    "usb",
    "high",
    "definition",
}


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

        self._logger.info(
            "Sender capture started in %s mode at %s Hz, %s-bit",
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
        if self._config.capture_processing == "unprocessed":
            try:
                self._capture_loop_unprocessed()
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if self._stop_event.is_set():
                    return
                self._logger.warning(
                    "Unprocessed capture could not stay active (%s). "
                    "Falling back to processed loopback for reliable capture.",
                    exc,
                )

        self._capture_loop_processed()

    def _capture_loop_unprocessed(self) -> None:
        device_index, device_name = self._select_unprocessed_input_device()
        self._logger.info(
            "Using unprocessed capture source: %s (Windows WDM-KS)",
            device_name,
        )

        target_frames = self._config.frame_samples
        frame_bytes = self._config.frame_bytes
        startup_silent_limit = max(
            1,
            int(
                (self._config.sample_rate / self._config.frame_samples)
                * UNPROCESSED_STARTUP_SILENCE_SECONDS
            ),
        )

        pending_bytes = bytearray()
        startup_silent_frames = 0
        startup_non_silent_seen = False
        fallback_to_processed = False

        def callback(indata, _frames, _time_info, status) -> None:
            nonlocal startup_silent_frames
            nonlocal startup_non_silent_seen
            nonlocal fallback_to_processed

            if status:
                self._logger.warning("Capture status warning: %s", status)

            if self._stop_event.is_set():
                raise sd.CallbackStop()

            pending_bytes.extend(bytes(indata))
            while len(pending_bytes) >= frame_bytes:
                frame = bytes(pending_bytes[:frame_bytes])
                del pending_bytes[:frame_bytes]

                if self._is_frame_effectively_silent(frame):
                    if not startup_non_silent_seen:
                        startup_silent_frames += 1
                        if startup_silent_frames >= startup_silent_limit:
                            fallback_to_processed = True
                            raise sd.CallbackAbort()
                else:
                    startup_non_silent_seen = True
                    startup_silent_frames = 0

                self._on_frame(frame, target_frames)

        with sd.RawInputStream(
            device=device_index,
            samplerate=self._config.sample_rate,
            channels=self._config.channels,
            blocksize=0,
            dtype=self._config.sounddevice_dtype,
            callback=callback,
            latency="low",
        ):
            self._started_event.set()
            while not self._stop_event.wait(0.2):
                if fallback_to_processed:
                    raise RuntimeError(
                        "startup monitor source stayed silent (likely mute-gated)"
                    )

    def _capture_loop_processed(self) -> None:
        com_initialized = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            com_initialized = True

            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default speaker available for loopback capture")

            mic = sc.get_microphone(speaker.name, include_loopback=True)
            self._logger.info("Using loopback source: %s", mic)

            with mic.recorder(
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                blocksize=self._config.frame_samples,
            ) as recorder:
                self._started_event.set()

                while not self._stop_event.is_set():
                    block = recorder.record(numframes=self._config.frame_samples)
                    frame = self._float_to_pcm_bytes(block)
                    self._on_frame(frame, self._config.frame_samples)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._startup_error = exc
            self._started_event.set()
            self._logger.exception("Loopback capture failed: %s", exc)
        finally:
            if com_initialized:
                ctypes.windll.ole32.CoUninitialize()

    def _is_frame_effectively_silent(self, frame: bytes) -> bool:
        if self._config.bit_depth == 16:
            samples = np.frombuffer(frame, dtype=np.int16)
            if samples.size == 0:
                return True
            peak = int(np.max(np.abs(samples)))
            return peak <= UNPROCESSED_SILENCE_INT16_THRESHOLD

        samples = np.frombuffer(frame, dtype=np.int32)
        if samples.size == 0:
            return True
        peak = int(np.max(np.abs(samples)))
        return peak <= UNPROCESSED_SILENCE_INT32_THRESHOLD

    def _select_unprocessed_input_device(self) -> tuple[int, str]:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        candidates: list[tuple[int, str]] = []
        for index, device in enumerate(devices):
            if int(device["max_input_channels"]) < self._config.channels:
                continue

            hostapi_name = str(hostapis[int(device["hostapi"])] ["name"])
            if hostapi_name != "Windows WDM-KS":
                continue

            candidates.append((index, str(device["name"])))

        if not candidates:
            raise RuntimeError(
                "No Windows WDM-KS input device is available for unprocessed capture"
            )

        for index, name in candidates:
            lowered = name.lower()
            if any(hint in lowered for hint in UNPROCESSED_NAME_HINTS):
                return index, name

        speaker_tokens = self._default_speaker_tokens()
        if speaker_tokens:
            best_score = 0
            best_match: tuple[int, str] | None = None
            for index, name in candidates:
                lowered = name.lower()
                score = sum(1 for token in speaker_tokens if token in lowered)
                if score > best_score:
                    best_score = score
                    best_match = (index, name)

            if best_match is not None:
                return best_match

        raise RuntimeError(
            "Could not find a monitor-style unprocessed input device "
            "(for example Stereo Mix)"
        )

    @staticmethod
    def _default_speaker_tokens() -> list[str]:
        speaker = sc.default_speaker()
        if speaker is None:
            return []

        raw_tokens = re.split(r"[^a-z0-9]+", speaker.name.lower())
        return [
            token
            for token in raw_tokens
            if len(token) >= 4 and token not in GENERIC_DEVICE_TOKENS
        ]

    def _float_to_pcm_bytes(self, block: np.ndarray) -> bytes:
        clipped = np.clip(block, -1.0, 1.0)
        if self._config.bit_depth == 16:
            pcm = (clipped * 32767.0).astype(np.int16)
            return pcm.tobytes(order="C")

        pcm = (clipped * 8388607.0).astype(np.int32)
        return pcm.tobytes(order="C")
