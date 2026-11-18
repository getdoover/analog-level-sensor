# Analog Level Sensor -- Development Guide

## Repository Structure

```
device/                       <-- The device app: Rust, on doover-rs (see device/README.md)
  doover_config.json          <-- Its Doover app entry
  Dockerfile                  <-- Static musl binary on scratch
  src/                        <-- Config, tags, UI, conversion, alarm, calibration
src/common/                   <-- The processor's shared Python: conversion, config, tags, UI
src/analog_level_sensor_processor/
  __init__.py               <-- Processor Lambda entry point
  application.py            <-- Message-path input handling
  app_config.py             <-- Processor config (input message path, subscriptions)
  app_tags.py               <-- Processor output tag wrapper
  app_ui.py                 <-- Processor UI wrapper
doover_config.json            <-- The processor's Doover app entry
```

The device app was Python too, sharing `src/common/` with the processor; it is
now `device/`, with the same config, UI and notification schemas, tags and
behaviour. The conversion and the shared UI exist in both languages: a change to
one belongs in the other.

## Architecture

### Signal Processing Pipeline

```
Analog Input → Sensor % → Level (m) → Fill %
                                                       ↓
                                              Volume Curve (optional)
```

1. **Raw reading** -- the device app reads `fetch_ai(pin)` from the platform interface; the processor app reads a configured message path such as `$on_dm_event.analog_input_v`
2. **Range validation** -- more than 3/16 of the input span (3 mA on 4-20 mA, so under 1 mA) below `sensor_min_mA` is a sensor fault (`sensor_fault`, debounced over 3 samples on the device; level tags cleared); within that margin it reads as the minimum
3. **Sensor percentage** -- maps the configured input range to 0-100% (inverted for radar sensors)
4. **Level reading** -- maps percentage to metres using sensor min/max
5. **Fill percentage** -- either linear mapping (empty→full) or volume curve interpolation

### Volume Curve

When configured with 2+ points, fill percentage is calculated from interpolated volume rather than linear level mapping. This handles non-linear tank geometries (e.g. cylindrical, conical).

### No State Machine

This app has no state machine -- it reads the sensor every loop iteration and publishes after a minimum warmup period (10 readings).

## Getting Started

The device app:

```bash
cd device
cargo test
DDA_URI=127.0.0.1:50051 PLT_URI=127.0.0.1:50053 APP_KEY=analog_level_sensor_1 cargo run --release
```

The processor:

```bash
uv sync
uv run pytest tests/
```

## Regenerating the schemas

```bash
cd device && cargo run -- export doover_config.json --app-name analog_level_sensor
uv run export-config-processor
uv run export-ui-processor
```
