from __future__ import annotations

import threading
from collections import deque

from .config import PROFILE_SETTINGS


class AdaptiveJitterBuffer:
    def __init__(
        self,
        *,
        frame_bytes: int,
        profile: str,
        max_frames: int = 100,
    ) -> None:
        self._frame_bytes = frame_bytes
        self._buffer: deque[bytes] = deque()
        self._lock = threading.Lock()
        settings = PROFILE_SETTINGS.get(profile)
        if settings is None:
            raise ValueError(f"profile must be one of {tuple(PROFILE_SETTINGS)}")

        self._max_frames = max(1, max_frames, settings.jitter_target_frames + settings.jitter_drop_margin)
        self._target_frames = settings.jitter_target_frames
        self._drop_margin = settings.jitter_drop_margin
        self._primed = False

    def reset(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._primed = False

    def push(self, frame: bytes) -> int:
        dropped = 0
        with self._lock:
            self._buffer.append(frame)
            if len(self._buffer) > self._max_frames:
                dropped = len(self._buffer) - self._max_frames
                for _ in range(dropped):
                    self._buffer.popleft()
        return dropped

    def pop(self) -> tuple[bytes, int, bool]:
        dropped = 0
        with self._lock:
            if not self._primed:
                if len(self._buffer) < self._target_frames:
                    return bytes(self._frame_bytes), dropped, True
                self._primed = True

            if not self._buffer:
                self._primed = False
                return bytes(self._frame_bytes), dropped, True

            if len(self._buffer) > self._target_frames + self._drop_margin:
                keep = self._target_frames + (self._drop_margin // 2)
                to_drop = len(self._buffer) - keep
                for _ in range(max(0, to_drop)):
                    self._buffer.popleft()
                    dropped += 1

            frame = self._buffer.popleft()
            if len(frame) != self._frame_bytes:
                self._primed = False
                return bytes(self._frame_bytes), dropped, True
            return frame, dropped, False

    def depth(self) -> int:
        with self._lock:
            return len(self._buffer)
