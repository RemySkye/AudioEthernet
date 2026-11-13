# AudioEthernet

AudioEthernet streams uncompressed PCM system audio over LAN between Windows 11 PCs with low latency, adaptive jitter buffering, and automatic receiver format synchronization.

## Install & Update

```bash
pip install "git+https://github.com/RemySkye/AudioEthernet.git"
```

To update an existing installation to the latest commit:

```bash
pip install --no-deps --force-reinstall "git+https://github.com/RemySkye/AudioEthernet.git"
```

After install, run `audioethernet` from PowerShell or CMD.

## Quick Start

On the receiver PC:

```bash
audioethernet -r
```

On the sender PC (regular loopback):

```bash
audioethernet -s
```

On the sender PC for **low-latency unprocessed audio** (via Stereo Mix / WDM-KS monitor source):

```bash
audioethernet -s --capture-processing unprocessed --latency-profile low
```

Receiver playback follows the sender's active stream format (`sample_rate`, `bit_depth`, `channels`, `frame_ms`) automatically.

## Latency Profiles (Sender-Controlled)

The **sender** chooses the stream format and `--latency-profile` (`balanced` is the default), and the **receiver** automatically syncs its playback and jitter buffer to match the sender:

- `low`: `5 ms` frames, `20 ms` capture/playback device ring buffers, and a tight adaptive jitter buffer (`3–10` frames, starting at `4`). Best for wired Gigabit Ethernet.
- `balanced`: Default (`5 ms` frames, `20 ms` capture/playback device ring buffers, `4–14` frame jitter buffer starting at `6`).
- `stable`: Larger buffers (`10 ms` frames, `20 ms` capture / `30 ms` playback device ring buffers, `5–18` frame jitter buffer starting at `8`) for noisier links or Wi-Fi.

Examples:

```bash
# Receiver just listens and adapts automatically to whatever the sender sends
audioethernet -r

# Sender: low-latency unprocessed 24-bit / 48 kHz stream (physical laptop speakers off via virtual cable if present)
audioethernet -s --capture-processing unprocessed --latency-profile low --bit-depth 24

# Sender: unprocessed while keeping hardware laptop speakers active via Stereo Mix
audioethernet -s --capture-processing unprocessed --keep-local-speakers

# Receiver: direct unicast connection to a known sender IP (skips broadcast discovery)
audioethernet -r --target 192.168.1.50

# Select specific audio devices by index or name substring
audioethernet -s --capture-processing unprocessed --capture-device "Stereo Mix"
audioethernet -r --playback-device "Headphones"
```

## Capture Modes

- `--capture-processing processed` (default): Captures Windows WASAPI loopback audio from the default output device (or `--capture-device`), emitting paced silence when nothing is playing so playback clocks stay smooth.
- `--capture-processing unprocessed`: Captures directly from a Windows WDM-KS monitor input (e.g., VB-Audio `CABLE Output` or `Stereo Mix`) without Windows APO loopback processing. When a virtual cable playback endpoint (such as VB-Audio `CABLE Input`) is available and neither `--capture-device` nor `--keep-local-speakers` is passed, AudioEthernet temporarily routes Windows default playback through the virtual cable so physical speakers stay completely off (even if muted in Windows) and restores your original speaker on exit. Use `--keep-local-speakers` to leave hardware speakers as the default output via `Stereo Mix`. Explicit `unprocessed` mode reports capture errors rather than silently falling back to `processed`.
- `--capture-processing auto`: Tries `unprocessed` first and automatically falls back to `processed` if unavailable or silent at startup.

## Supported Formats & Ports

- **Sample rates:** `44100`, `48000`, `96000` Hz
- **Bit depths:** `16`, `24`, `32`-bit (stereo; `24`-bit is MSB-aligned in a 32-bit container)
- **Frame durations:** `2`, `5`, `10`, `20` ms
- **UDP ports:**
  - Control / discovery: `50481` (`--control-port`) on the sender
  - Audio data: `50482` (`--data-port`) on the receiver
- When launched with administrator privileges on Windows, the sender opens inbound UDP `50481` and the receiver opens inbound UDP `50482` in Windows Defender Firewall automatically.
