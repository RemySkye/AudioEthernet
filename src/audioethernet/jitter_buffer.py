from __future__ import annotations

import threading
import time
from typing import Optional

from .config import PROFILE_SETTINGS, ProfileSettings


def _seq_diff(a: int, b: int) -> int:
    diff = (a - b) & 0xFFFFFFFF
    if diff >= 0x80000000:
        return diff - 0x100000000
    return diff


class AdaptiveJitterBuffer:
    def __init__(
        self,
        *,
        frame_bytes: int,
        latency_profile: str,
        max_frames: int = 100,
        frame_duration_seconds: float = 0.005,
        profile_settings: Optional[ProfileSettings] = None,
    ) -> None:
        self._frame_bytes = frame_bytes
        self._silence = bytes(frame_bytes)
        self._lock = threading.Lock()
        self._max_frames = max(1, max_frames)
        self._frame_duration = max(0.0005, frame_duration_seconds)

        settings = profile_settings or PROFILE_SETTINGS.get(
            latency_profile, PROFILE_SETTINGS["balanced"]
        )
        self._min_target_frames = settings.jitter_min_frames
        self._initial_target_frames = settings.jitter_target_frames
        self._max_target_frames = settings.jitter_max_frames
        self._target_frames = settings.jitter_target_frames
        self._drop_margin = settings.jitter_drop_margin

        self._slots: dict[int, bytes] = {}
        self._primed = False
        self._initialized = False
        self._next_seq = 0
        self._auto_seq = 0

        self._last_arrival_time: Optional[float] = None
        self._last_arrival_seq: Optional[int] = None
        self._jitter_frames = 0.0
        self._stable_pops = 0

        self._lost_packets = 0
        self._late_packets = 0

    def reset(self) -> None:
        with self._lock:
            self._slots.clear()
            self._primed = False
            self._initialized = False
            self._next_seq = 0
            self._auto_seq = 0
            self._target_frames = self._initial_target_frames
            self._last_arrival_time = None
            self._last_arrival_seq = None
            self._jitter_frames = 0.0
            self._stable_pops = 0

    def push(self, frame: bytes, sequence: Optional[int] = None) -> int:
        if len(frame) != self._frame_bytes:
            return 1

        now = time.monotonic()
        dropped = 0

        with self._lock:
            if sequence is None:
                seq = self._auto_seq
                self._auto_seq = (self._auto_seq + 1) & 0xFFFFFFFF
            else:
                seq = sequence & 0xFFFFFFFF

            if not self._initialized:
                self._initialized = True
                self._next_seq = seq
            else:
                delta_from_next = _seq_diff(seq, self._next_seq)
                if delta_from_next < -self._max_frames:
                    # Sender likely restarted and reset sequence counter.
                    self._slots.clear()
                    self._primed = False
                    self._next_seq = seq
                    self._last_arrival_time = None
                    self._last_arrival_seq = None
                elif delta_from_next < 0:
                    self._late_packets += 1
                    return 1
                elif delta_from_next > self._max_frames * 4:
                    # Large forward jump (e.g. sender resumed after pause).
                    self._slots.clear()
                    self._primed = False
                    self._next_seq = seq
                    self._last_arrival_time = None
                    self._last_arrival_seq = None

            if seq in self._slots:
                return 0

            if (
                self._last_arrival_time is not None
                and self._last_arrival_seq is not None
            ):
                seq_step = _seq_diff(seq, self._last_arrival_seq)
                if 0 < seq_step <= 16:
                    expected_dt = seq_step * self._frame_duration
                    actual_dt = now - self._last_arrival_time
                    diff_frames = abs(actual_dt - expected_dt) / self._frame_duration
                    self._jitter_frames += (diff_frames - self._jitter_frames) / 16.0
                    desired = int(self._jitter_frames + 1.5)
                    if desired > self._target_frames:
                        self._target_frames = min(self._max_target_frames, desired)
                        self._stable_pops = 0

            self._last_arrival_time = now
            self._last_arrival_seq = seq
            self._slots[seq] = frame

            if len(self._slots) > self._max_frames:
                ordered = sorted(
                    self._slots.keys(), key=lambda s: _seq_diff(s, self._next_seq)
                )
                overflow = len(self._slots) - self._max_frames
                for old_seq in ordered[:overflow]:
                    del self._slots[old_seq]
                    dropped += 1
                if ordered[overflow:]:
                    oldest_kept = ordered[overflow]
                    if _seq_diff(oldest_kept, self._next_seq) > 0:
                        self._next_seq = oldest_kept

        return dropped

    def pop(self) -> tuple[bytes, int, bool]:
        dropped = 0
        with self._lock:
            if not self._primed:
                if len(self._slots) < self._target_frames:
                    return self._silence, dropped, True
                self._primed = True
                if self._slots and self._next_seq not in self._slots:
                    oldest_seq = min(
                        self._slots.keys(), key=lambda s: _seq_diff(s, self._next_seq)
                    )
                    if _seq_diff(oldest_seq, self._next_seq) > 0:
                        self._next_seq = oldest_seq

            if not self._slots:
                self._primed = False
                self._stable_pops = 0
                if self._target_frames < self._max_target_frames:
                    self._target_frames += 1
                return self._silence, dropped, True

            if len(self._slots) > self._target_frames + self._drop_margin:
                keep = self._target_frames + (self._drop_margin // 2)
                ordered = sorted(
                    self._slots.keys(), key=lambda s: _seq_diff(s, self._next_seq)
                )
                to_drop = max(0, len(ordered) - keep)
                for old_seq in ordered[:to_drop]:
                    del self._slots[old_seq]
                    dropped += 1
                if to_drop > 0 and ordered[to_drop:]:
                    self._next_seq = ordered[to_drop]

            frame = self._slots.pop(self._next_seq, None)
            if frame is not None:
                self._next_seq = (self._next_seq + 1) & 0xFFFFFFFF
                self._stable_pops += 1
                if (
                    self._stable_pops >= 400
                    and self._target_frames > self._min_target_frames
                    and self._jitter_frames < max(0.5, self._target_frames - 1.5)
                ):
                    self._target_frames -= 1
                    self._stable_pops = 0
                return frame, dropped, False

            # Current sequence slot missing while newer packets are buffered.
            oldest_seq = min(
                self._slots.keys(), key=lambda s: _seq_diff(s, self._next_seq)
            )
            gap = _seq_diff(oldest_seq, self._next_seq)
            if gap > self._max_frames:
                self._next_seq = (oldest_seq + 1) & 0xFFFFFFFF
                self._lost_packets += 1
                self._stable_pops = 0
                return self._slots.pop(oldest_seq), dropped, False

            self._next_seq = (self._next_seq + 1) & 0xFFFFFFFF
            self._lost_packets += 1
            self._stable_pops = 0
            return self._silence, dropped, True

    def depth(self) -> int:
        with self._lock:
            return len(self._slots)

    def target_frames(self) -> int:
        with self._lock:
            return self._target_frames

    def packet_counters(self) -> tuple[int, int]:
        with self._lock:
            return self._lost_packets, self._late_packets
