
# Analog Level Sensor

<img src="https://doover.com/wp-content/uploads/Doover-Logo-Landscape-Navy-padded-small.png" alt="App Icon" style="max-width: 300px;">

**Doover application for analog level sensors**

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/getdoover/analog-level-sensor)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/getdoover/analog-level-sensor/blob/main/LICENSE)

[Getting Started](#getting-started) • [Configuration](#configuration) • [Developer](https://github.com/getdoover/analog-level-sensor/blob/main/DEVELOPMENT.md) • [Need Help?](#need-help)

<br/>

## Overview

Monitors water level using analog readings such as 4-20 mA or voltage inputs. The Docker device app reads a local analog input, while the processor app reads a configured path from subscribed cloud messages. Both variants share the same level, percentage, volume, tags, and UI logic.

Key capabilities:

- **Sensor types** -- submersible (low input=low level), radar (low input=high level), and radar inverted
- **Volume curves** -- converts level to volume using configurable interpolation points
- **Fill percentage** -- calculates percentage fill from empty/full level thresholds or volume curve
- **Sensor power control** -- optional digital output to power the sensor on/off
- **Remote processing** -- processor variant can convert an input reading from a subscribed message into the same level tags and UI

<br/>

## Getting Started

### Configuration

| Setting | Description | Default |
|---------|-------------|---------|
| **AI Pin** | Analog input pin number | *required* |
| **Sensor Maximum Metres** | Maximum sensor depth (m) | *required* |
| **Full Level** | Level reading when full (m) | *required* |
| **Input Units** | Units of the raw input reading | `mA` |
| **Sensor Min/Max Input** | Sensor output range in the configured input units | `4.0` / `20.0` |
| **Sensor Min Metres** | Minimum sensor depth (m) | `0.0` |
| **Empty Level** | Level reading when empty (m) | `0.0` |
| **Power Pin** | Digital output pin to power the sensor | `null` |
| **Sensor Type** | Submersible / Radar / Radar Inverted | `Submersible` |
| **Volume Curve** | Array of level/volume points for interpolation | `[]` |

Processor-only configuration:

| Setting | Description | Default |
|---------|-------------|---------|
| **Input Message Path** | Message path containing the raw analog reading. Use `$channel.path.to.value` | `$on_dm_event.analog_input_v` |

<br/>

### Config compatibility

The alarm settings (`alarm_enabled`, `alarm_type`, `alarm_source`,
`alarm_slider_minimum`, `alarm_slider_maximum`, `alarm_grace_period_s`,
`alarm_renotify_interval_s`) are deliberately kept as **flat, top-level config
keys** rather than being grouped under a nested `AlarmConfig` object. This is
intentional and must not be "tidied" into a nested block: deployed instances
store their config against these flat keys, so nesting them would silently drop
the stored alarm settings on the next config load. A flat optional scalar also
lets an older deployment config fall back to its default instead of raising.
See the explanatory comment in `src/analog_level_sensor/app_config.py`.

<br/>

## Integrations

### Tags

| Tag | Type | Description |
|-----|------|-------------|
| **level_filled_percentage** | number | Fill percentage (0-100+%) |
| **level_reading** | number | Calculated level in metres |
| **raw_level_reading** | number | Raw analog reading in the configured input units |
| **level_volume** | number | Calculated volume, when enabled |

### Dependencies

- **Platform Interface** -- analog input reading and optional power pin control
- **Message Subscription** -- processor variant input message path

<br/>

### Need Help?

- Email: support@doover.com
- [Doover Documentation](https://docs.doover.com)

<br/>

## License

This app is licensed under the [Apache License 2.0](https://github.com/getdoover/analog-level-sensor/blob/main/LICENSE).
