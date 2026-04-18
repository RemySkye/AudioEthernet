from __future__ import annotations

import threading
from collections import deque


class AdaptiveJitterBuffer:
    def __init__(
        self,
        *,
        frame_bytes: int,
        latency_profile: str,
        max_frames: int = 100,
    ) -> None:
        self._frame_bytes = frame_bytes
        self._buffer: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._max_frames = max_frames
        self._target_frames = self._initial_target_for_profile(latency_profile)
        self._drop_margin = self._drop_margin_for_profile(latency_profile)
        self._primed = False

    @staticmethod
    def _initial_target_for_profile(latency_profile: str) -> int:
        if latency_profile == "low":
            return 5
        if latency_profile == "stable":
            return 10
        return 5

    @staticmethod
    def _drop_margin_for_profile(latency_profile: str) -> int:
        if latency_profile == "low":
            return 8
        if latency_profile == "stable":
            return 14
        return 10

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
