# Analog Level Sensor -- Development Guide

## Repository Structure

```
src/common/
  common_app.py             <-- Shared conversion logic and tag updates
  common_config.py          <-- Shared level sensor config schema
  common_tags.py            <-- Shared output tags
  common_ui.py              <-- Shared UI state
src/analog_level_sensor/
  __init__.py               <-- Docker device entry point
  application.py            <-- Hardware polling and power-pin handling
  app_config.py             <-- Device config (AI pin, power pin, polling)
  app_tags.py               <-- Device tag wrapper
  app_ui.py                 <-- Device UI wrapper
src/analog_level_sensor_processor/
  __init__.py               <-- Processor Lambda entry point
  application.py            <-- Message-path input handling
  app_config.py             <-- Processor config (input message path, subscriptions)
  app_tags.py               <-- Processor output tag wrapper
  app_ui.py                 <-- Processor UI wrapper
```

## Architecture

### Signal Processing Pipeline

```
Analog Input → Sensor % → Level (m) → Fill %
                                                       ↓
                                              Volume Curve (optional)
```

1. **Raw reading** -- the Docker app reads `platform_iface.fetch_ai(pin)`; the processor app reads a configured message path such as `$on_dm_event.analog_input_v`
2. **Range validation** -- ignores values below `sensor_min_mA`
3. **Sensor percentage** -- maps the configured input range to 0-100% (inverted for radar sensors)
4. **Level reading** -- maps percentage to metres using sensor min/max
5. **Fill percentage** -- either linear mapping (empty→full) or volume curve interpolation

### Volume Curve

When configured with 2+ points, fill percentage is calculated from interpolated volume rather than linear level mapping. This handles non-linear tank geometries (e.g. cylindrical, conical).

### No State Machine

This app has no state machine -- it reads the sensor every loop iteration and publishes after a minimum warmup period (10 readings).

## Getting Started

```bash
uv sync
uv run pytest tests/
```

```bash
cd simulators
docker compose up --build
```

## Regenerating doover_config.json

```bash
uv run export-config
uv run export-ui
uv run export-config-processor
uv run export-ui-processor
```
