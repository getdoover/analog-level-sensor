# Analog level sensor — Rust port

A Rust rewrite of this app, built on **[doover-rs](https://github.com/getdoover/doover-rs)**
(the Rust equivalent of `pydoover`). It is a faithful port of the Python app in
`../src/analog_level_sensor` — same config schema, same tags, same UI, same
mA→level/percentage/volume maths (volume curve and Radar inversion included),
same "below 4 mA = disconnected, skip" rule — but ships as a ~2 MB static
binary.

## Why

- **Footprint**: a `scratch`-based image of a few MB and a small RSS, vs the
  Python app's interpreter + deps. Matters on RAM-constrained CM4-class
  devices.
- **Type safety**: the deployment config, tags and UI are all declared as typed
  Rust structs via `doover-rs`'s derive macros, and the config schema is
  exported straight from them.

## Layout

Unlike the Python app (many small modules), the Rust port keeps everything in
one file that maps 1:1 onto the Python structure:

- `src/main.rs`
  - `AnalogLevelSensorConfig` (`#[derive(Config)]`) — the typed deployment
    config, including `VolumeCurvePoint` / `SensorType` and `ApplicationPosition`
    (mirrors `app_config.py`).
  - `AnalogLevelSensorTags` (`#[derive(Tags)]`) — `level_filled_percentage`,
    `level_reading`, `raw_level_reading`, `level_volume` (mirrors `app_tags.py`).
  - `AnalogLevelSensorUi` (`#[derive(Ui)]` + `UiBuild`) — the radial gauge and
    readings, including the runtime volume-promotion in `setup` (mirrors
    `app_ui.py`).
  - `AnalogLevelSensor` (`impl Application`) — `setup` / `main_loop` /
    `on_shutdown`, the mA→level/%/volume maths, power-pin handling, and the
    `SIMULATE_AI` fallback (mirrors `application.py`).
- `Cargo.toml`, `Dockerfile`.

## Config

The typed `AnalogLevelSensorConfig` produces the **same** `config_schema` keys
as the Python app (`ai_pin`, `sensor_maximum_metres`, `full_level`,
`sensor_minimum_ma`, `sensor_maximum_ma`, `sensor_minimum_metres`,
`empty_level`, `power_pin`, `polling_frequency`, `sensor_type`, `volume_curve`,
`hide_volume`, `max_volume`, `volume_units`, `volume_decimal_precision`,
`dv_app_position`). At runtime the config is read from the `deployment_config`
channel (keyed by `APP_KEY`).

Regenerate the repo's `doover_config.json` from the Rust schema (the built-in
`export` subcommand — the counterpart of the Python `export-config`):

```sh
cargo run -- export ../doover_config.json --app-name analog_level_sensor
```

## Env

- `DDA_URI` (default `127.0.0.1:50051`) — the device agent.
- `PLT_URI` (default `127.0.0.1:50053`) — the platform interface.
- `APP_KEY` — the app instance key (drives config + tag namespacing).
- `SIMULATE_AI` — test only. Synthesise a noisy in-range mA value instead of
  reading the platform interface, to exercise the loop without the platform
  serial round-trip.

## Build

During local development the app depends on the sibling `doover-rs` checkout by
**path** (`../../doover-rs/doover` in `Cargo.toml`), so it tracks library
changes immediately:

```sh
# local dev — requires ../../doover-rs alongside this repo
cd rust && cargo build --release

# regenerate config + run
cargo run -- export ../doover_config.json --app-name analog_level_sensor
DDA_URI=127.0.0.1:50051 PLT_URI=127.0.0.1:50053 APP_KEY=analog_level_sensor_1 \
  cargo run --release
```

Because the dependency is a path, the container image must be built from the
directory that holds **both** repos (see the `Dockerfile` header):

```sh
cd ~/Coding/doover
docker build -f analog-level-sensor/rust/Dockerfile \
  -t ghcr.io/getdoover/analog-level-sensor-rs:main .
```

For a self-contained CI build, pin `doover-rs` as a git dependency in
`Cargo.toml` (see the comment there) and build with just `rust/` as context.
