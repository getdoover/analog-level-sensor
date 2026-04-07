# Analog Level Sensor -- Development Guide

## Repository Structure

```
src/analog_level_sensor/
  __init__.py               <-- Entry point
  application.py            <-- Sensor reading, Kalman filtering, level/volume calculation
  app_config.py             <-- Config schema (pins, sensor range, volume curve)
  app_tags.py               <-- Tags (filled percentage, level reading, raw reading)
  app_ui.py                 <-- UI (percentage gauge, level display)
```

## Architecture

### Signal Processing Pipeline

```
4-20mA Analog Input → Kalman Filter → Sensor % → Level (m) → Fill %
                                                       ↓
                                              Volume Curve (optional)
```

1. **Raw reading** -- `platform_iface.fetch_ai(pin)` returns mA value
2. **Range validation** -- rejects values outside `sensor_min_mA` to `sensor_max_mA`
3. **Kalman filter** -- smooths noise with configurable variance
4. **Sensor percentage** -- maps mA to 0-100% (inverted for radar sensors)
5. **Level reading** -- maps percentage to metres using sensor min/max
6. **Fill percentage** -- either linear mapping (empty→full) or volume curve interpolation

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
```
