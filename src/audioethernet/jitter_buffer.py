from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

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
        bit_depth: int = 16,
        channels: int = 2,
    ) -> None:
        self._frame_bytes = frame_bytes
        self._silence = bytes(frame_bytes)
        self._lock = threading.Lock()
        self._max_frames = max(1, max_frames)
        self._frame_duration = max(0.0005, frame_duration_seconds)
        self._bit_depth = bit_depth
        self._channels = max(1, channels)

        bytes_per_sample = 2 if bit_depth == 16 else 4
        frame_stride = self._channels * bytes_per_sample
        self._pcm_smooth_enabled = (
            frame_stride > 0
            and frame_bytes >= frame_stride * 8
            and (frame_bytes % frame_stride) == 0
        )
        self._pcm_dtype = np.int16 if bit_depth == 16 else np.int32

        settings = profile_settings or PROFILE_SETTINGS.get(
            latency_profile, PROFILE_SETTINGS["balanced"]
        )
        self._profile_frame_ms = settings.frame_ms
        self._base_min_target_frames = settings.jitter_min_frames
        self._base_initial_target_frames = settings.jitter_target_frames
        self._base_max_target_frames = settings.jitter_max_frames
        self._base_drop_margin = settings.jitter_drop_margin

        self._min_target_frames = settings.jitter_min_frames
        self._initial_target_frames = settings.jitter_target_frames
        self._max_target_frames = settings.jitter_max_frames
        self._target_frames = settings.jitter_target_frames
        self._drop_margin = settings.jitter_drop_margin
        self._stable_pop_threshold = 400

        self._slots: dict[int, bytes] = {}
        self._primed = False
        self._initialized = False
        self._next_seq = 0
        self._auto_seq = 0

        self._last_arrival_time: Optional[float] = None
        self._last_arrival_seq: Optional[int] = None
        self._jitter_frames = 0.0
        self._stable_pops = 0
        self._underrun_streak = 0
        self._need_fade_in = False
        self._last_frame: Optional[bytes] = None

        self._lost_packets = 0
        self._late_packets = 0

    def configure_timing(self, frame_duration_seconds: float) -> None:
        with self._lock:
            self._frame_duration = max(0.0005, frame_duration_seconds)
            profile_duration = self._profile_frame_ms / 1000.0
            scale = max(1.0, profile_duration / self._frame_duration)
            self._min_target_frames = max(
                1, int(round(self._base_min_target_frames * scale))
            )
            self._initial_target_frames = max(
                self._min_target_frames,
                int(round(self._base_initial_target_frames * scale)),
            )
            self._max_target_frames = max(
                self._initial_target_frames,
                int(round(self._base_max_target_frames * scale)),
            )
            self._drop_margin = max(2, int(round(self._base_drop_margin * scale)))
            self._stable_pop_threshold = max(400, int(round(1000 * scale)))
            if not self._primed:
                self._target_frames = self._initial_target_frames
            else:
                self._target_frames = max(
                    self._min_target_frames,
                    min(self._max_target_frames, self._target_frames),
                )

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
            self._underrun_streak = 0
            self._need_fade_in = False
            self._last_frame = None

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
                    self._underrun_streak = 0
                    self._need_fade_in = False
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
                    self._underrun_streak = 0
                    self._need_fade_in = False

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

    def _smooth_underrun_frame(self) -> bytes:
        if (
            not self._pcm_smooth_enabled
            or self._last_frame is None
            or self._underrun_streak > 1
        ):
            return self._silence
        arr = np.frombuffer(self._last_frame, dtype=self._pcm_dtype).reshape(
            -1, self._channels
        )
        n = arr.shape[0]
        if n == 0:
            return self._silence
        # Ramp smoothly down to silence over up to 48 samples (~1ms at 48kHz)
        # to eliminate vertical step clicks when an underrun begins.
        ramp_len = min(n, 48)
        fade = np.zeros(n, dtype=np.float64)
        fade[:ramp_len] = np.linspace(1.0, 0.0, ramp_len, endpoint=False)
        scaled = np.rint(arr.astype(np.float64) * fade[:, None]).astype(self._pcm_dtype)
        return scaled.tobytes(order="C")

    def _fade_in_frame(self, frame: bytes) -> bytes:
        if not self._pcm_smooth_enabled:
            return frame
        arr = np.frombuffer(frame, dtype=self._pcm_dtype).reshape(-1, self._channels)
        n = arr.shape[0]
        if n < 2:
            return frame
        ramp_len = min(n, 48)
        out = arr.astype(np.float64)
        fade = np.linspace(0.0, 1.0, ramp_len, endpoint=True)[:, None]
        out[:ramp_len] *= fade
        return np.rint(out).astype(self._pcm_dtype).tobytes(order="C")

    def _crossfade_splice(self, outgoing: bytes, incoming: bytes) -> bytes:
        if not self._pcm_smooth_enabled:
            return incoming
        arr_out = np.frombuffer(outgoing, dtype=self._pcm_dtype).reshape(
            -1, self._channels
        )
        arr_in = np.frombuffer(incoming, dtype=self._pcm_dtype).reshape(
            -1, self._channels
        )
        n = min(arr_out.shape[0], arr_in.shape[0])
        if n < 2:
            return incoming
        ramp_len = min(n, 48)
        blended = arr_in.astype(np.float64)
        w = np.linspace(0.0, 1.0, ramp_len, endpoint=True)[:, None]
        blended[:ramp_len] = (arr_out[:ramp_len].astype(np.float64) * (1.0 - w)) + (
            blended[:ramp_len] * w
        )
        return np.rint(blended).astype(self._pcm_dtype).tobytes(order="C")

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
                # Keep _primed = True across brief transient empties so a 1-2ms
                # phase slip between sender capture and playback never stalls into
                # a multi-frame silence mute. Grow target once per underrun streak.
                self._underrun_streak += 1
                self._stable_pops = 0
                if (
                    self._underrun_streak == 1
                    and self._target_frames < self._max_target_frames
                ):
                    self._target_frames += 1
                self._need_fade_in = True
                return self._smooth_underrun_frame(), dropped, True

            last_dropped: Optional[bytes] = None
            if len(self._slots) > self._target_frames + self._drop_margin:
                keep = self._target_frames + max(2, self._drop_margin // 3)
                ordered = sorted(
                    self._slots.keys(), key=lambda s: _seq_diff(s, self._next_seq)
                )
                to_drop = max(0, len(ordered) - keep)
                for old_seq in ordered[:to_drop]:
                    last_dropped = self._slots.pop(old_seq)
                    dropped += 1
                if to_drop > 0 and ordered[to_drop:]:
                    self._next_seq = ordered[to_drop]

            frame = self._slots.pop(self._next_seq, None)
            if frame is not None:
                self._next_seq = (self._next_seq + 1) & 0xFFFFFFFF
                self._underrun_streak = 0
                self._stable_pops += 1
                if (
                    self._stable_pops >= self._stable_pop_threshold
                    and self._target_frames > self._min_target_frames
                    and self._jitter_frames
                    < max(0.5, (self._target_frames - self._min_target_frames) - 0.5)
                ):
                    self._target_frames -= 1
                    self._stable_pops = 0

                if self._need_fade_in:
                    self._need_fade_in = False
                    frame = self._fade_in_frame(frame)
                elif last_dropped is not None:
                    frame = self._crossfade_splice(last_dropped, frame)

                self._last_frame = frame
                return frame, dropped, False

            # Current sequence slot missing while newer packets are buffered.
            oldest_seq = min(
                self._slots.keys(), key=lambda s: _seq_diff(s, self._next_seq)
            )
            gap = _seq_diff(oldest_seq, self._next_seq)
            if gap > self._max_frames:
                self._next_seq = (oldest_seq + 1) & 0xFFFFFFFF
                self._lost_packets += 1
                self._underrun_streak = 0
                self._stable_pops = 0
                popped = self._slots.pop(oldest_seq)
                if self._last_frame is not None:
                    popped = self._crossfade_splice(self._last_frame, popped)
                self._last_frame = popped
                return popped, dropped, False

            self._next_seq = (self._next_seq + 1) & 0xFFFFFFFF
            self._lost_packets += 1
            self._underrun_streak += 1
            self._stable_pops = 0
            self._need_fade_in = True
            return self._smooth_underrun_frame(), dropped, True

    def depth(self) -> int:
        with self._lock:
            return len(self._slots)

    def target_frames(self) -> int:
        with self._lock:
            return self._target_frames

    def packet_counters(self) -> tuple[int, int]:
        with self._lock:
            return self._lost_packets, self._late_packets
