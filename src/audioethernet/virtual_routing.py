from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import sys
import threading
import uuid
from typing import Any, Callable, Iterable, Optional

import soundcard as sc

from .config import StreamConfig

VIRTUAL_SPEAKER_HINTS = (
    "cable input",
    "vb-audio",
    "virtual cable",
    "voicemeeter",
)

CLSID_POLICY_CONFIG_CLIENT = "{870af99c-171d-4f9e-af0d-e63df40c2bc9}"
IID_IPOLICY_CONFIG = "{f8679f50-850a-41cf-9c72-430f290290c8}"
_CLSCTX_ALL = 0x17
_COINIT_MULTITHREADED = 0x0
_RPC_E_CHANGED_MODE = -2147417850
_ROLES = (0, 1, 2)  # eConsole, eMultimedia, eCommunications

_CONSOLE_CTRL_HANDLER_REFS: list[Any] = []


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: str) -> "_GUID":
        parsed = uuid.UUID(value)
        d1, d2, d3, d4, d5, node = parsed.fields
        tail = bytes([d4, d5]) + node.to_bytes(6, "big")
        return cls(d1, d2, d3, (ctypes.c_ubyte * 8).from_buffer_copy(tail))


def set_default_audio_endpoint(device_id: str) -> None:
    """Switch the Windows default playback endpoint for all roles via IPolicyConfig."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("Switching default playback endpoint is only supported on Windows")

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.HRESULT

    init_hr = ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    should_uninit = init_hr in (0, 1)
    if not should_uninit and init_hr != _RPC_E_CHANGED_MODE:
        raise OSError(f"CoInitializeEx failed with HRESULT 0x{init_hr & 0xFFFFFFFF:08X}")

    ptr = ctypes.c_void_p()
    try:
        clsid = _GUID.from_uuid(CLSID_POLICY_CONFIG_CLIENT)
        iid = _GUID.from_uuid(IID_IPOLICY_CONFIG)
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            _CLSCTX_ALL,
            ctypes.byref(iid),
            ctypes.byref(ptr),
        )
        if hr < 0 or not ptr.value:
            raise OSError(
                f"CoCreateInstance(IPolicyConfig) failed with HRESULT 0x{hr & 0xFFFFFFFF:08X}"
            )

        vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_fn_type = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD
        )
        release_fn_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        set_default = set_fn_type(vtable[13])
        release = release_fn_type(vtable[2])

        try:
            for role in _ROLES:
                role_hr = set_default(ptr, device_id, role)
                if role_hr < 0:
                    raise OSError(
                        f"IPolicyConfig::SetDefaultEndpoint(role={role}) failed "
                        f"with HRESULT 0x{role_hr & 0xFFFFFFFF:08X}"
                    )
        finally:
            release(ptr)
    finally:
        if should_uninit:
            ole32.CoUninitialize()


def _select_virtual_speaker(speakers: Iterable[Any]) -> Optional[Any]:
    speaker_list = list(speakers)
    for hint in VIRTUAL_SPEAKER_HINTS:
        for speaker in speaker_list:
            if hint in str(getattr(speaker, "name", "")).lower():
                return speaker
    return None


def _speaker_is_virtual(speaker: Any) -> bool:
    name = str(getattr(speaker, "name", "")).lower()
    return any(hint in name for hint in VIRTUAL_SPEAKER_HINTS)


class LocalSpeakerMuteGuard:
    """Temporarily routes Windows default playback to a virtual cable in unprocessed mode."""

    def __init__(
        self,
        config: StreamConfig,
        logger,
        *,
        all_speakers_fn: Optional[Callable[[], list[Any]]] = None,
        default_speaker_fn: Optional[Callable[[], Any]] = None,
        set_default_endpoint_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._all_speakers = all_speakers_fn or sc.all_speakers
        self._default_speaker = default_speaker_fn or sc.default_speaker
        self._set_default_endpoint = set_default_endpoint_fn or set_default_audio_endpoint
        self._lock = threading.Lock()
        self._original_id: Optional[str] = None
        self._original_name: Optional[str] = None
        self._active_virtual_name: Optional[str] = None
        self._atexit_registered = False

    def activate(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        if self._config.role != "sender":
            return False
        if self._config.capture_processing not in {"unprocessed", "auto"}:
            return False
        if self._config.capture_device or self._config.keep_local_speakers:
            return False

        with self._lock:
            if self._original_id is not None:
                return True

            try:
                current = self._default_speaker()
                virtual = _select_virtual_speaker(self._all_speakers())
            except Exception as exc:
                self._logger.debug("Virtual speaker lookup skipped: %s", exc)
                return False

            if virtual is None:
                return False
            if current is not None and (
                getattr(current, "id", None) == getattr(virtual, "id", None)
                or _speaker_is_virtual(current)
            ):
                return False
            if current is None or not getattr(current, "id", None):
                return False

            try:
                self._set_default_endpoint(str(virtual.id))
            except Exception as exc:
                self._logger.warning(
                    "Could not switch Windows playback to virtual cable '%s': %s",
                    getattr(virtual, "name", "unknown"),
                    exc,
                )
                return False

            self._original_id = str(current.id)
            self._original_name = str(getattr(current, "name", "original speaker"))
            self._active_virtual_name = str(getattr(virtual, "name", "virtual cable"))

            if not self._atexit_registered:
                atexit.register(self.restore)
                self._register_console_ctrl_handler()
                self._atexit_registered = True

            self._logger.info(
                "Routed Windows default playback temporarily from '%s' to '%s' "
                "(laptop speakers off; use --keep-local-speakers to keep hardware speakers active)",
                self._original_name,
                self._active_virtual_name,
            )
            return True

    def restore(self) -> None:
        with self._lock:
            original_id = self._original_id
            original_name = self._original_name
            if original_id is None:
                return
            self._original_id = None
            self._original_name = None
            self._active_virtual_name = None

        try:
            self._set_default_endpoint(original_id)
            self._logger.info(
                "Restored Windows default playback to '%s'",
                original_name or original_id,
            )
        except Exception as exc:
            self._logger.warning(
                "Failed to restore Windows default playback to '%s': %s",
                original_name or original_id,
                exc,
            )

    def _register_console_ctrl_handler(self) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            kernel32 = ctypes.windll.kernel32
            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            def _on_ctrl(_ctrl_type: int) -> bool:
                self.restore()
                return False

            callback = handler_type(_on_ctrl)
            if kernel32.SetConsoleCtrlHandler(callback, True):
                _CONSOLE_CTRL_HANDLER_REFS.append(callback)
        except Exception:
            pass
