# AudioEthernet

AudioEthernet streams system audio over LAN between Windows PCs. It is tuned for low CPU use, safe defaults, and automatic receiver format sync.

## Install

```bash
pip install "git+https://github.com/<your-username>/AudioEthernet.git"
```

After install, run `audioethernet` from PowerShell or CMD.

## Quick Start

On the receiver PC:

```bash
audioethernet -r
```

On the sender PC:

```bash
audioethernet -s
```

Receiver playback follows the sender's active stream format automatically, so the receiver does not need to match the sender manually.
Both roles use the same UDP port, so only one port needs to be allowed on the machine.

## Profiles

- `-p safe` is the default. It uses larger buffers and more delay margin to stay stable.
- `-p low` reduces playback delay while staying inside a safer buffer range.

Examples:

```bash
audioethernet -s -p safe
audioethernet -s -p low --sample-rate 96000 --bit-depth 24
audioethernet -r -p low
```

## Default Behavior

- Sender capture defaults to processed loopback.
- Receiver defaults to the safe profile.
- Stereo is used by default.
- The default sender format is 16-bit, 48000 Hz.
- The sender and receiver both use UDP port 50482 by default.
- The receiver tries to allow that single UDP port through Windows Firewall when it starts on Windows with administrator privileges.

## Supported Formats

- Sample rates: 32000, 44100, 48000, 88200, 96000 Hz
- Bit depths: 16, 24, 32-bit

## Capture Modes

- `--capture-processing processed` is the default and captures regular Windows loopback audio.
- `--capture-processing unprocessed` uses Stereo Mix or a similar WDM-KS monitor source. The sender device must be unmuted so the monitor mix actually contains audio.

If unprocessed capture is unavailable or silent, the sender can fall back to processed loopback automatically.
