# AudioEthernet

AudioEthernet streams system audio over LAN between Windows 11 machines.

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
- 5 ms frame size

## Useful Options

```bash
audioethernet -s --bit-depth 24 --sample-rate 48000
audioethernet -s --latency-profile low --frame-ms 5
audioethernet -s --capture-processing processed
```

## Receiver Format Sync

- Receiver now auto-syncs to sender stream format (bit depth, sample rate, and frame size).
- You can keep receiver command simple: `audioethernet -r`.
- Sender format controls the active stream format.

## Capture Processing Modes

- `--capture-processing unprocessed` (default): tries to capture without endpoint enhancement chains when a suitable monitor source is available.
- `--capture-processing processed`: captures regular loopback including endpoint effects.

If unprocessed monitor capture is not usable on a device, sender falls back to processed loopback automatically.

If you see a warning that unprocessed capture could not stay active, common causes are monitor input gating or disabled Stereo Mix style paths on the driver.

Quick fixes:

- Start playback audio before launching sender.
- In Windows Sound Recording, enable and unmute Stereo Mix (or equivalent monitor input).
- Keep speaker endpoint unmuted and set non-zero volume while testing.
- If your driver blocks monitor capture, use `--capture-processing processed`.
