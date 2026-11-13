# AudioEthernet

AudioEthernet streams uncompressed PCM system audio over LAN between Windows 11 PCs with low latency, adaptive jitter buffering, and automatic receiver format synchronization.

## Install

```bash
pip install "git+https://github.com/RemySkye/AudioEthernet.git"
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

- `low`: Smallest WASAPI/WDM-KS device buffers (`10 ms`) and tight adaptive jitter buffer (`2–6` frames, starting at `3`). Best for wired Gigabit Ethernet.
- `balanced`: Default (`20 ms` device buffers, `3–10` frame jitter buffer, starting at `4`).
- `stable`: Larger buffers (`40 ms` device buffers, `10 ms` frames, `5–16` frame jitter buffer) for noisier links or Wi-Fi.

Examples:

```bash
# Receiver just listens and adapts automatically to whatever the sender sends
audioethernet -r

# Sender: low-latency unprocessed 24-bit / 48 kHz stream
audioethernet -s --capture-processing unprocessed --latency-profile low --bit-depth 24

# Receiver: direct unicast connection to a known sender IP (skips broadcast discovery)
audioethernet -r --target 192.168.1.50

# Select specific audio devices by index or name substring
audioethernet -s --capture-processing unprocessed --capture-device "Stereo Mix"
audioethernet -r --playback-device "Headphones"
```

## Capture Modes

- `--capture-processing processed` (default): Captures Windows WASAPI loopback audio from the default output device (or `--capture-device`).
- `--capture-processing unprocessed`: Captures directly from a Windows WDM-KS monitor input (e.g., Stereo Mix) without Windows APO loopback processing. Keep the sender output unmuted so the monitor mix carries audio. Falls back to processed loopback only if opening the WDM-KS device fails.
- `--capture-processing auto`: Tries `unprocessed` first and automatically falls back to `processed` if unavailable or silent at startup.

## Supported Formats & Ports

- **Sample rates:** `44100`, `48000`, `96000` Hz
- **Bit depths:** `16`, `24`, `32`-bit (stereo; `24`-bit is MSB-aligned in a 32-bit container)
- **Frame durations:** `2`, `5`, `10`, `20` ms
- **UDP ports:**
  - Control / discovery: `50481` (`--control-port`) on the sender
  - Audio data: `50482` (`--data-port`) on the receiver
- When launched with administrator privileges on Windows, the sender opens inbound UDP `50481` and the receiver opens inbound UDP `50482` in Windows Defender Firewall automatically.
