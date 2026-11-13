from __future__ import annotations

import contextlib
import ctypes
import re
import threading
import time
from typing import Callable, Iterator, Optional

import numpy as np
import soundcard as sc
import sounddevice as sd

from .config import StreamConfig

AudioFrameCallback = Callable[[bytes, int], None]
ErrorCallback = Callable[[Exception], None]

UNPROCESSED_SILENCE_INT16_THRESHOLD = 64
UNPROCESSED_SILENCE_INT32_THRESHOLD = 4096 * 256

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
        on_error: Optional[ErrorCallback] = None,
    ) -> None:
        self._config = config
        self._on_frame = on_frame
        self._logger = logger
        self._on_error = on_error
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._startup_error: Exception | None = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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
        mode = self._config.capture_processing
        if mode in {"unprocessed", "auto"}:
            try:
                self._capture_loop_unprocessed(abort_on_silence=(mode == "auto"))
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if self._stop_event.is_set():
                    return
                self._logger.warning(
                    "Unprocessed capture could not stay active (%s). "
                    "Falling back to processed loopback.",
                    exc,
                )

        self._capture_loop_processed()

    def _capture_loop_unprocessed(self, *, abort_on_silence: bool) -> None:
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
                (self._config.sample_rate / target_frames)
                * self._config.unprocessed_silence_seconds
            ),
        )

        pending_bytes = bytearray()
        startup_silent_frames = 0
        startup_non_silent_seen = False
        startup_silence_warned = False
        fallback_to_processed = False
        callback_status_warnings = 0
        last_status_log = 0.0

        def callback(indata, _frames, _time_info, status) -> None:
            nonlocal startup_silent_frames
            nonlocal startup_non_silent_seen
            nonlocal fallback_to_processed
            nonlocal callback_status_warnings

            if status:
                callback_status_warnings += 1

            if self._stop_event.is_set():
                raise sd.CallbackStop()

            pending_bytes.extend(indata)
            while len(pending_bytes) >= frame_bytes:
                frame = bytes(pending_bytes[:frame_bytes])
                del pending_bytes[:frame_bytes]

                if not startup_non_silent_seen:
                    if self._is_frame_effectively_silent(frame):
                        startup_silent_frames += 1
                        if (
                            abort_on_silence
                            and startup_silent_frames >= startup_silent_limit
                        ):
                            fallback_to_processed = True
                            raise sd.CallbackAbort()
                    else:
                        startup_non_silent_seen = True
                        startup_silent_frames = 0

                self._on_frame(frame, target_frames)

        try:
            with self._open_unprocessed_stream(
                device_index=device_index,
                target_frames=target_frames,
                callback=callback,
            ):
                self._started_event.set()
                while not self._stop_event.wait(0.2):
                    if callback_status_warnings:
                        now = time.monotonic()
                        if now - last_status_log >= 5.0:
                            self._logger.warning(
                                "Capture status warnings observed: %s",
                                callback_status_warnings,
                            )
                            callback_status_warnings = 0
                            last_status_log = now

                    if (
                        not abort_on_silence
                        and not startup_non_silent_seen
                        and not startup_silence_warned
                        and startup_silent_frames >= startup_silent_limit
                    ):
                        startup_silence_warned = True
                        self._logger.warning(
                            "Unprocessed capture has seen only silence for %.1fs; "
                            "make sure the sender output is unmuted and audio is playing.",
                            self._config.unprocessed_silence_seconds,
                        )

                    if fallback_to_processed:
                        raise RuntimeError(
                            "startup monitor source stayed silent (likely mute-gated)"
                        )
        except sd.PortAudioError:
            raise

    @contextlib.contextmanager
    def _open_unprocessed_stream(
        self,
        *,
        device_index: int,
        target_frames: int,
        callback,
    ) -> Iterator[sd.RawInputStream]:
        try:
            stream = sd.RawInputStream(
                device=device_index,
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                blocksize=target_frames,
                dtype=self._config.sounddevice_dtype,
                callback=callback,
                latency=self._config.profile_settings.capture_latency_seconds,
            )
        except sd.PortAudioError:
            stream = sd.RawInputStream(
                device=device_index,
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                blocksize=0,
                dtype=self._config.sounddevice_dtype,
                callback=callback,
                latency=self._config.profile_settings.capture_latency_seconds,
            )

        with stream:
            yield stream

    def _select_processed_microphone(self):
        if self._config.capture_device:
            needle = self._config.capture_device.strip().lower()
            all_mics = sc.all_microphones(include_loopback=True)
            for mic in all_mics:
                if needle in mic.name.lower():
                    return mic
            raise RuntimeError(
                f"Requested capture device '{self._config.capture_device}' not found"
            )

        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("No default speaker available for loopback capture")
        return sc.get_microphone(speaker.name, include_loopback=True)

    def _capture_loop_processed(self) -> None:
        com_initialized = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            com_initialized = True

            mic = self._select_processed_microphone()
            self._logger.info("Using loopback source: %s", mic)

            target_frames = self._config.frame_samples
            ring_buffer_frames = max(
                target_frames * 16, self._config.sample_rate // 10
            )
            with mic.recorder(
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                blocksize=ring_buffer_frames,
            ) as recorder:
                self._started_event.set()
                self._drain_processed_recorder(recorder, target_frames)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            already_started = self._started_event.is_set()
            if not already_started:
                self._startup_error = exc
                self._started_event.set()
            self._logger.exception("Loopback capture failed: %s", exc)
            if already_started and not self._stop_event.is_set() and self._on_error:
                self._on_error(exc)
        finally:
            if com_initialized:
                ctypes.windll.ole32.CoUninitialize()

    def _drain_processed_recorder(self, recorder, target_frames: int) -> None:
        if not hasattr(recorder, "_capture_available_frames"):
            while not self._stop_event.is_set():
                block = recorder.record(numframes=target_frames)
                frame = self._float_to_pcm_bytes(block)
                self._on_frame(frame, target_frames)
            return

        channels = self._config.channels
        pending = np.empty((0, channels), dtype=np.float32)
        frame_duration = target_frames / self._config.sample_rate
        last_emit = time.monotonic()
        silent_frame = bytes(self._config.frame_bytes)
        poll_sleep = min(0.002, max(0.0005, frame_duration * 0.4))

        while not self._stop_event.is_set():
            avail = recorder._capture_available_frames()
            if avail > 0:
                block = recorder.record(numframes=None)
                if len(block) > 0:
                    if len(pending) == 0:
                        pending = block
                    else:
                        pending = np.concatenate((pending, block), axis=0)
                    offset = 0
                    total = len(pending)
                    while (total - offset) >= target_frames:
                        slice_block = pending[offset : offset + target_frames]
                        self._on_frame(
                            self._float_to_pcm_bytes(slice_block), target_frames
                        )
                        offset += target_frames
                        last_emit = time.monotonic()
                    if offset > 0:
                        pending = pending[offset:]
                continue

            now = time.monotonic()
            if (now - last_emit) >= frame_duration * 2.5:
                if len(pending) > 0:
                    padded = np.zeros((target_frames, channels), dtype=np.float32)
                    copy_n = min(target_frames, len(pending))
                    padded[:copy_n] = pending[:copy_n]
                    pending = np.empty((0, channels), dtype=np.float32)
                    self._on_frame(self._float_to_pcm_bytes(padded), target_frames)
                else:
                    self._on_frame(silent_frame, target_frames)
                last_emit += frame_duration
                if (now - last_emit) > frame_duration * 2.0:
                    last_emit = now - frame_duration
            else:
                time.sleep(poll_sleep)

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

        requested = (
            self._config.capture_device.strip()
            if self._config.capture_device
            else None
        )
        if requested is not None:
            if requested.isdigit():
                idx = int(requested)
                if 0 <= idx < len(devices):
                    dev = devices[idx]
                    if int(dev["max_input_channels"]) >= self._config.channels:
                        return idx, str(dev["name"])
                    raise RuntimeError(
                        f"Capture device {idx} ({dev['name']}) does not support {self._config.channels} input channels"
                    )
                raise RuntimeError(f"Capture device index {idx} out of range")

            needle = requested.lower()
            # Prefer WDM-KS match first, then any input device matching the name.
            for prefer_wdmks in (True, False):
                for index, device in enumerate(devices):
                    if int(device["max_input_channels"]) < self._config.channels:
                        continue
                    hostapi_name = str(hostapis[int(device["hostapi"])]["name"])
                    if prefer_wdmks and hostapi_name != "Windows WDM-KS":
                        continue
                    name = str(device["name"])
                    if needle in name.lower():
                        return index, name

            raise RuntimeError(
                f"Could not find input capture device matching '{requested}'"
            )

        candidates: list[tuple[int, str]] = []
        for index, device in enumerate(devices):
            if int(device["max_input_channels"]) < self._config.channels:
                continue

            hostapi_name = str(hostapis[int(device["hostapi"])]["name"])
            if hostapi_name != "Windows WDM-KS":
                continue

            candidates.append((index, str(device["name"])))

        if not candidates:
            raise RuntimeError(
                "No Windows WDM-KS input device is available for unprocessed capture"
            )

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

        for index, name in candidates:
            lowered = name.lower()
            if any(hint in lowered for hint in UNPROCESSED_NAME_HINTS):
                return index, name

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
            pcm = np.rint(clipped * 32767.0).astype(np.int16)
            return pcm.tobytes(order="C")

        if self._config.bit_depth == 24:
            # PortAudio int32 streams expect MSB-aligned 24-bit samples.
            pcm24 = np.rint(clipped.astype(np.float64) * 8388607.0).astype(np.int32)
            pcm = np.left_shift(pcm24, 8)
            return pcm.tobytes(order="C")

        scaled = np.rint(clipped.astype(np.float64) * 2147483647.0)
        pcm = np.clip(scaled, -2147483648.0, 2147483647.0).astype(np.int32)
        return pcm.tobytes(order="C")
