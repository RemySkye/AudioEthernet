from __future__ import annotations

import ctypes
import threading
import time
from typing import Callable

import numpy as np
import soundcard as sc
from comtypes import COMMETHOD, GUID, IUnknown
from pycaw.pycaw import AudioUtilities, IAudioClient

from .config import StreamConfig

AudioFrameCallback = Callable[[bytes, int], None]

# RAW loopback startup grace window before fallback.
RAW_STARTUP_SILENCE_SECONDS = 8.0
RAW_SILENCE_INT16_THRESHOLD = 64
RAW_SILENCE_INT32_THRESHOLD = 4096
PROCESSED_RECORDER_BLOCK_MULTIPLIER = 4
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002
AUDCLNT_STREAMOPTIONS_RAW = 0x1
AUDCLNT_STREAMOPTIONS_MATCH_FORMAT = 0x2
AUDIO_CATEGORY_MEDIA = 11
REFERENCE_TIME_100NS_PER_SEC = 10_000_000
RAW_BUFFER_DURATION_100NS = int(0.2 * REFERENCE_TIME_100NS_PER_SEC)

HRESULT = ctypes.HRESULT
UINT32 = ctypes.c_uint32
BYTE = ctypes.c_ubyte
DWORD = ctypes.wintypes.DWORD
BOOL = ctypes.wintypes.BOOL

AUDCLNT_E_UNSUPPORTED_FORMAT = -2004287480
AUDCLNT_E_ENGINE_FORMAT_LOCKED = -2004287471
AUDCLNT_E_ENGINE_PERIODICITY_LOCKED = -2004287470
AUDCLNT_E_DEVICE_INVALIDATED = -2004287485
AUDCLNT_E_RESOURCES_INVALIDATED = -2004287486


def _describe_raw_exception(exc: Exception) -> str:
    if not exc.args:
        return repr(exc)

    code = exc.args[0]
    if not isinstance(code, int):
        return repr(exc)

    mapping = {
        AUDCLNT_E_UNSUPPORTED_FORMAT: "endpoint rejected RAW stream format",
        AUDCLNT_E_ENGINE_FORMAT_LOCKED: "audio engine format is locked by another client",
        AUDCLNT_E_ENGINE_PERIODICITY_LOCKED: (
            "audio engine periodicity is locked by another client"
        ),
        AUDCLNT_E_DEVICE_INVALIDATED: "audio endpoint was invalidated",
        AUDCLNT_E_RESOURCES_INVALIDATED: "audio stream resources were invalidated",
    }
    reason = mapping.get(code)
    if reason is None:
        return repr(exc)
    return f"{reason} (HRESULT {code})"


class AudioClientProperties(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT32),
        ("bIsOffload", BOOL),
        ("eCategory", DWORD),
        ("Options", DWORD),
    ]


class IAudioClient2(IAudioClient):
    _iid_ = GUID("{726778CD-F60A-4EDA-82DE-E47610CD78AA}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "IsOffloadCapable",
            (["in"], DWORD, "Category"),
            (["out"], ctypes.POINTER(BOOL), "pbOffloadCapable"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetClientProperties",
            (["in"], ctypes.POINTER(AudioClientProperties), "pProperties"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetBufferSizeLimits",
            (["in"], ctypes.c_void_p, "pFormat"),
            (["in"], BOOL, "bEventDriven"),
            (["out"], ctypes.POINTER(ctypes.c_longlong), "phnsMinBufferDuration"),
            (["out"], ctypes.POINTER(ctypes.c_longlong), "phnsMaxBufferDuration"),
        ),
    )


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "GetBuffer",
            (["out"], ctypes.POINTER(ctypes.POINTER(BYTE)), "ppData"),
            (["out"], ctypes.POINTER(UINT32), "pNumFramesToRead"),
            (["out"], ctypes.POINTER(DWORD), "pdwFlags"),
            (["out"], ctypes.POINTER(ctypes.c_uint64), "pu64DevicePosition"),
            (["out"], ctypes.POINTER(ctypes.c_uint64), "pu64QPCPosition"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "ReleaseBuffer",
            (["in"], UINT32, "NumFramesRead"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetNextPacketSize",
            (["out"], ctypes.POINTER(UINT32), "pNumFramesInNextPacket"),
        ),
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
        if self._config.capture_processing == "raw":
            self._set_active_processing_mode("raw")
            try:
                self._capture_loop_raw()
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if self._stop_event.is_set():
                    return
                self._logger.warning(
                    "RAW loopback capture could not stay active (%s). "
                    "Falling back to processed loopback for reliable capture.",
                    _describe_raw_exception(exc),
                )

        self._set_active_processing_mode("processed")
        self._capture_loop_processed()

    def _capture_loop_raw(self) -> None:
        com_initialized = False
        audio_client = None
        capture_client = None
        started = False

        try:
            ctypes.windll.ole32.CoInitialize(None)
            com_initialized = True

            speakers = AudioUtilities.GetSpeakers()
            if speakers is None:
                raise RuntimeError("No default render endpoint available for RAW loopback")

            self._logger.info(
                "Using RAW WASAPI loopback endpoint: %s",
                speakers.FriendlyName,
            )

            audio_client = speakers._dev.Activate(IAudioClient._iid_, 23, None).QueryInterface(
                IAudioClient
            )

            try:
                audio_client2 = audio_client.QueryInterface(IAudioClient2)
                props = AudioClientProperties()
                props.cbSize = ctypes.sizeof(AudioClientProperties)
                props.bIsOffload = False
                props.eCategory = AUDIO_CATEGORY_MEDIA
                props.Options = AUDCLNT_STREAMOPTIONS_RAW | AUDCLNT_STREAMOPTIONS_MATCH_FORMAT
                audio_client2.SetClientProperties(props)
            except Exception:
                # If RAW options are unsupported on this device/driver, Initialize will decide.
                pass

            mix_fmt_ptr = audio_client.GetMixFormat()
            mix_fmt = mix_fmt_ptr.contents
            bytes_per_sample = max(1, int(mix_fmt.wBitsPerSample) // 8)
            frame_bytes = self._config.frame_samples * self._config.channels * bytes_per_sample
            startup_silent_limit = max(
                1,
                int(
                    (self._config.sample_rate / self._config.frame_samples)
                    * RAW_STARTUP_SILENCE_SECONDS
                ),
            )
            startup_silent_frames = 0
            startup_non_silent_seen = False
            overflow_count = 0
            pending_bytes = bytearray()

            self._logger.info(
                "RAW loopback negotiated endpoint mix format: %s Hz, %s channels, %s-bit",
                int(mix_fmt.nSamplesPerSec),
                int(mix_fmt.nChannels),
                int(mix_fmt.wBitsPerSample),
            )

            audio_client.Initialize(
                0,
                AUDCLNT_STREAMFLAGS_LOOPBACK,
                RAW_BUFFER_DURATION_100NS,
                0,
                mix_fmt_ptr,
                None,
            )

            capture_client = audio_client.GetService(IAudioCaptureClient._iid_).QueryInterface(
                IAudioCaptureClient
            )
            audio_client.Start()
            started = True
            self._started_event.set()

            while not self._stop_event.is_set():
                available = capture_client.GetNextPacketSize()
                if available == 0:
                    time.sleep(0.001)
                    continue

                while available:
                    data_ptr = ctypes.POINTER(BYTE)()
                    frames_to_read = UINT32()
                    flags = DWORD()
                    capture_client.GetBuffer(
                        ctypes.byref(data_ptr),
                        ctypes.byref(frames_to_read),
                        ctypes.byref(flags),
                        None,
                        None,
                    )

                    read_frames = int(frames_to_read.value)
                    expected_bytes = read_frames * self._config.channels * bytes_per_sample

                    if flags.value & AUDCLNT_BUFFERFLAGS_SILENT:
                        chunk_bytes = bytes(max(0, expected_bytes))
                    else:
                        chunk_bytes = ctypes.string_at(data_ptr, expected_bytes)

                    if read_frames != target_frames:
                        overflow_count += 1
                        if overflow_count == 1 or (overflow_count % 25) == 0:
                            self._logger.warning(
                                "RAW loopback packet size mismatch (frames=%s expected=%s count=%s)",
                                read_frames,
                                target_frames,
                                overflow_count,
                            )

                    capture_client.ReleaseBuffer(frames_to_read)
                    pending_bytes.extend(chunk_bytes)
                    while len(pending_bytes) >= frame_bytes:
                        frame = bytes(pending_bytes[:frame_bytes])
                        del pending_bytes[:frame_bytes]

                        if self._is_frame_effectively_silent(frame):
                            if not startup_non_silent_seen:
                                startup_silent_frames += 1
                                if startup_silent_frames >= startup_silent_limit:
                                    raise RuntimeError(
                                        "startup RAW loopback stream stayed silent for "
                                        f"{RAW_STARTUP_SILENCE_SECONDS:.1f}s"
                                    )
                        else:
                            startup_non_silent_seen = True
                            startup_silent_frames = 0

                        self._on_frame(frame, target_frames)
                    available = capture_client.GetNextPacketSize()
        except Exception:
            if not self._started_event.is_set():
                self._started_event.set()
            raise
        finally:
            if started and audio_client is not None:
                try:
                    audio_client.Stop()
                except Exception:
                    pass
            if com_initialized:
                ctypes.windll.ole32.CoUninitialize()

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

            with mic.recorder(
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                blocksize=self._config.frame_samples
                * PROCESSED_RECORDER_BLOCK_MULTIPLIER,
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
            return peak <= RAW_SILENCE_INT16_THRESHOLD

        samples = np.frombuffer(frame, dtype=np.int32)
        if samples.size == 0:
            return True
        peak = int(np.max(np.abs(samples)))
        return peak <= RAW_SILENCE_INT32_THRESHOLD

    def _float_to_pcm_bytes(self, block: np.ndarray) -> bytes:
        clipped = np.clip(block, -1.0, 1.0)
        if self._config.bit_depth == 16:
            pcm = (clipped * 32767.0).astype(np.int16)
            return pcm.tobytes(order="C")

        pcm = (clipped * 8388607.0).astype(np.int32)
        return pcm.tobytes(order="C")
