# Analog level sensor — Rust port

A Rust rewrite of the device app, built on
**[doover-rs](https://github.com/getdoover/doover-rs)** (the Rust equivalent of
`pydoover`). It is meant as a drop-in replacement for the Python app in
`../src/analog_level_sensor` (+ `../src/common`): the same config schema, UI
schema and notification schema (the export is checked byte for byte against
the Python app's), the same tags, and the same behaviour:

- mA → level / filled percentage / volume, with the volume curve, Radar
  inversion, fluid density scaling of a submersible's column, and a negative
  zero (minimum level below the tank datum);
- `level_reading` stays in metres; `level_reading_display` carries the
  configured Depth Units for this app's own gauge;
- sub-4 mA handling: anything within 3/16 of the input span below the minimum
  (1–4 mA on 4–20 mA) reads as the minimum; only a dead loop (< 1 mA) raises
  `sensor_fault` after 3 samples, clears the derived tags, shows the warning,
  and takes 3 good samples to clear ("Sensor input back in range");
- the alarm (`../ALARM_SYSTEM.md`): Greater Than / Less Than / Allowed Range on
  the Reading Type (or the deprecated Alarm Source for a legacy null), grace
  period, re-notify interval, per-depth-unit slider keys, sent as the declared
  `level_alarm` notification;
- Operator Sensor Calibration: zero / span / fluid density set from the UI or
  the HMI over `ui_cmds` RPCs (`INVALID` / `UNAVAILABLE` errors as in Python),
  stored in `ui_cmds`, readback tags, reset to configured values, and the
  offline-boot restore from the readback tags.

The processor variant (`../src/analog_level_sensor_processor`, a cloud lambda)
is not ported.

## Layout

| Python                                       | Rust                 |
|----------------------------------------------|----------------------|
| `common/common_config.py`, `app_config.py`   | `src/config.rs`      |
| `common/common_tags.py`, `app_tags.py`       | `src/tags.rs`        |
| `common/common_ui.py`, `app_ui.py`           | `src/ui.rs`          |
| `common/common_app.py`                       | `src/conversion.rs`  |
| `alarm.py`                                   | `src/alarm.rs`       |
| `application.py` (alarm wiring)              | `src/device.rs`      |
| `calibration.py`                             | `src/calibration.rs` |
| `app_notifications.py`, `application.py`     | `src/main.rs`        |

The Python tests are ported alongside each module (`cargo test`).

## Schemas

`cargo run -- export ../doover_config.json --app-name analog_level_sensor`
writes the config, UI and notification schemas. `src/main.rs` has a test that
requires them to equal the Python app's export, kept in
`tests/fixtures/python_export.json`; after changing the Python app, refresh it
with `scripts/check-export.sh` (needs `uv`).

## Env

- `DDA_URI` (default `localhost:50051`), `PLT_URI` (default `127.0.0.1:50053`),
  `APP_KEY` — as for any Doover app.
- `RUST_LOG` — log filter (default `info`). The per-sample reading is logged at
  `debug` (Python logs it at INFO, which at 40 Hz costs a lot of CPU).
- `SIMULATE_AI` — test only: synthesise a noisy in-range mA value instead of
  reading the platform interface.

## Build

doover-rs is a pinned git dependency (a release tag in `Cargo.toml`). To work
against a local checkout without editing it:

```sh
cargo build --config 'patch."https://github.com/getdoover/doover-rs".doover.path="../../doover-rs/doover"'
```

The image (a static musl binary on `scratch`) builds from the repo root:

```sh
docker build -f rust/Dockerfile -t analog-level-sensor-rs:dev .
```
