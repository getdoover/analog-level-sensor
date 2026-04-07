
# Analog Level Sensor

<img src="https://doover.com/wp-content/uploads/Doover-Logo-Landscape-Navy-padded-small.png" alt="App Icon" style="max-width: 300px;">

**Doover application for analog (4-20mA) level sensors**

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/getdoover/analog-level-sensor)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/getdoover/analog-level-sensor/blob/main/LICENSE)

[Getting Started](#getting-started) • [Configuration](#configuration) • [Developer](https://github.com/getdoover/analog-level-sensor/blob/main/DEVELOPMENT.md) • [Need Help?](#need-help)

<br/>

## Overview

Monitors water level using analog 4-20mA sensors. Supports both submersible and radar sensor types with configurable volume curves for accurate capacity calculations.

Key capabilities:

- **Sensor types** -- submersible (4mA=low), radar (4mA=high), and radar inverted
- **Kalman filtering** -- smooths noisy analog readings with configurable measurement variance
- **Volume curves** -- converts level to volume using configurable interpolation points
- **Fill percentage** -- calculates percentage fill from empty/full level thresholds or volume curve
- **Sensor power control** -- optional digital output to power the sensor on/off

<br/>

## Getting Started

### Configuration

| Setting | Description | Default |
|---------|-------------|---------|
| **AI Pin** | Analog input pin number | *required* |
| **Sensor Maximum Metres** | Maximum sensor depth (m) | *required* |
| **Full Level** | Level reading when full (m) | *required* |
| **Sensor Min/Max mA** | Sensor output range | `4.0` / `20.0` |
| **Sensor Min Metres** | Minimum sensor depth (m) | `0.0` |
| **Empty Level** | Level reading when empty (m) | `0.0` |
| **Power Pin** | Digital output pin to power the sensor | `null` |
| **Sensor Type** | Submersible / Radar / Radar Inverted | `Submersible` |
| **Measurement Variance** | Kalman filter variance | `0.5` |
| **Volume Curve** | Array of level/volume points for interpolation | `[]` |

<br/>

## Integrations

### Tags

| Tag | Type | Description |
|-----|------|-------------|
| **level_filled_percentage** | number | Fill percentage (0-100+%) |
| **level_reading** | number | Calculated level in metres |
| **raw_level_reading** | number | Raw analog reading in mA |

### Dependencies

- **Platform Interface** -- analog input reading and optional power pin control

<br/>

### Need Help?

- Email: support@doover.com
- [Doover Documentation](https://docs.doover.com)

<br/>

## License

This app is licensed under the [Apache License 2.0](https://github.com/getdoover/analog-level-sensor/blob/main/LICENSE).
