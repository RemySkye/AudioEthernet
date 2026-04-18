# AudioEthernet

AudioEthernet streams system audio over LAN between Windows 10/11 machines.

## Compatibility

- Python 3.9+
- Windows 10 and Windows 11

## Install From GitHub

```bash
pip install "git+https://github.com/<your-username>/AudioEthernet.git"
```

After install, the `audioethernet` command is available in PowerShell and CMD.

## Quick Start

On receiver PC:

```bash
audioethernet -r
```

On sender PC:

```bash
audioethernet -s
```

Keep both terminal windows open while streaming.

## Defaults

- Stereo
- 16-bit
- 48000 Hz
- 5 ms frame size (low-latency default)
- Low receiver latency profile

## Useful Options

```bash
audioethernet -s --bit-depth 24 --sample-rate 48000
audioethernet -s --latency-profile low --frame-ms 5
audioethernet -s --latency-profile stable --frame-ms 10  # more buffering on unstable networks
```

## Receiver Format Sync

- Receiver now auto-syncs to sender stream format (bit depth, sample rate, and frame size).
- You can keep receiver command simple: `audioethernet -r`.
- Sender format controls the active stream format.

## Capture Processing Modes

- Processed loopback is the only capture mode now.
- It captures regular loopback including endpoint effects.

Quality tuning currently includes:

- Low-latency recorder blocks with fixed-size frame slicing before network send.
- Dithered 16-bit conversion and rounded PCM conversion to reduce quantization artifacts.

Quick quality tips:

- Start playback audio before launching sender.
- Use 24-bit mode when possible for best headroom: `audioethernet -s --bit-depth 24`.

Important limitation (Windows drivers):

- Endpoint enhancement settings (driver/APO) can still affect processed loopback sound.
