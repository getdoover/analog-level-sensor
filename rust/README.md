# Analog level sensor — Rust port

A Rust rewrite of this app, built on **[doover-rs](https://github.com/getdoover/doover-rs)**
(the Rust equivalent of `pydoover`). It is behaviourally faithful to the Python
app in `../src/` — same config keys, same mA→level/percentage/volume maths, same
"below 4 mA = disconnected, skip" rule — but ships as a ~2 MB static binary.

## Why

- **Footprint**: ~2 MB `scratch`-based image and ~2–17 MB RSS, vs the Python
  app's interpreter + deps. Matters on RAM-constrained CM4-class devices.
- **Headroom**: the app loop and live-tag streaming sustain the configured rate
  with room to spare (verified at 50 Hz on a CM4 — see below).

## Layout

- `src/main.rs` — the whole app: an `Application` that reads the AI pin from the
  platform interface, publishes `raw_level_reading` / `level_reading` /
  `level_filled_percentage` / (optionally) `level_volume` tags, and declares
  those as live tags.
- `Cargo.toml`, `Dockerfile`.

## Config

Read from the `deployment_config` channel (keyed by `APP_KEY`), same keys as the
Python app: `ai_pin`, `sensor_minimum_ma`, `sensor_maximum_ma`,
`sensor_minimum_metres`, `sensor_maximum_metres`, `empty_level`, `full_level`,
`max_volume`, `hide_volume`, `volume_decimal_precision`, `polling_frequency`.

## Env

- `DDA_URI` (default `127.0.0.1:50051`) — the device agent.
- `PLT_URI` (default `127.0.0.1:50053`) — the platform interface.
- `APP_KEY` — the app instance key (drives config + tag namespacing).
- `SIMULATE_AI` — test only. Synthesise a noisy in-range mA value instead of
  reading the platform interface, to exercise the loop / live-mode ceiling
  without the platform serial round-trip.

## Build

This app depends on [`doover-rs`](https://github.com/getdoover/doover-rs) as a
git dependency, pinned to a revision in `Cargo.toml` (the `pydoover`-from-PyPI
equivalent; switch to a version tag once doover-rs cuts releases). Nothing else
is needed — cargo fetches it.

```sh
# local dev
cd rust && cargo build --release

# container image (build from the repo root)
docker build -f rust/Dockerfile -t ghcr.io/getdoover/analog-level-sensor-rs:main .
```

## Verified on a CM4 (doovit)

- Loads config from `deployment_config` via `APP_KEY` (e.g. `ai_pin: 2`).
- Reads the live platform interface (`getAI`) — ~0 mA on an unwired pin,
  correctly skipped below the 4 mA floor.
- Publishes tags to the live device agent (visible in the cloud).
- **Live mode**: a tag opened in the UI's live mode streams as one-shots at the
  loop rate; nothing when unobserved.
- With `SIMULATE_AI` (no platform read), the loop and live streaming run at the
  full 50 Hz — confirming the ~5 Hz real-world rate is the platform serial read,
  not the app or the device agent.
