
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
| **Fluid Density** | Fluid density (kg/m³); scales submersible readings from metres of water to metres of fluid. Ignored for radar | `1000.0` (water) |
| **Volume Curve** | Array of level/volume points for interpolation | `[]` |
| **Depth Units** | Unit for the Level Reading gauge, alarm slider and alarm message (`m`/`cm`/`mm`/`in`/`ft`). The `level_reading` tag stays in metres | `m` |
| **Operator Sensor Calibration** | Device app only. Lets operators set the zero, span and fluid density on site (see below) | `false` |

> **Changing Depth Units resets a level alarm's setpoint.** The Alarm Point / Allowed
> Range sliders are stored per depth unit, so switching units presents a fresh, unset
> slider on the new scale instead of reinterpreting the old number in the new unit
> (which would turn a 8 m setpoint into 8 mm). The alarm stays quiet until the point is
> set again; switching back to the previous unit restores the setpoint it had there.
> Alarms on Filled Percentage or Volume are unaffected.

#### Operator Sensor Calibration

With **Operator Sensor Calibration** on, a **Sensor Calibration** section in the app's UI
(and the local HMI, through the same RPCs) sets three values without a redeploy. Each
defaults to its config field, applies from the next reading, and feeds every derived
reading (level, percentage, volume, alarm):

| Value / RPC / tag | Meaning | Range | Default |
|-------------------|---------|-------|---------|
| `zero_m` | Minimum level (m): at the minimum input (4 mA); at 20 mA for a Radar | -100 <= zero < span | Sensor Min Metres |
| `span_m` | Maximum level (m): at the maximum input (20 mA); at 4 mA for a Radar | 0 < span <= 100, above zero | Sensor Maximum Metres |
| `fluid_density` | kg/m³, scales submersible readings | 500 - 2500 | Fluid Density (1000) |

Always metres, whatever Depth Units is. A Radar reads inverted (4 mA is the maximum
level), so the section labels its zero as the level at 20 mA and its span as the level
at 4 mA. The zero may be negative, for a sensor whose minimum-level end sits below the
tank datum (e.g. a submersible transmitter whose 4 mA point is 0.15 m below the tank
floor: zero -0.15 m); the span stays above 0 m. Near that end the level then reads
below 0 m, and the filled percentage and volume follow it linearly, unclamped (slightly
negative), as they do for any level below the Empty Level. **Reset to configured values** (RPC
`reset_calibration`) returns all three to the config. An out-of-range or non-numeric
value is refused with `INVALID`; with the setting off every RPC is refused with
`UNAVAILABLE`, the section is hidden and the tags below are not published. No
notification is sent on a change.

Processor-only configuration:

| Setting | Description | Default |
|---------|-------------|---------|
| **Input Message Path** | Message path containing the raw analog reading. Use `$channel.path.to.value` | `$on_dm_event.analog_input_v` |

<br/>

## Integrations

### Tags

| Tag | Type | Description |
|-----|------|-------------|
| **level_filled_percentage** | number | Fill percentage (0-100+%) |
| **level_reading** | number | Calculated level in metres (canonical -- peer apps read this) |
| **level_reading_display** | number | Calculated level converted to the configured Depth Units, for this app's own gauge |
| **raw_level_reading** | number | Raw analog reading in the configured input units, published every sample (a fault included) |
| **level_volume** | number | Calculated volume, when enabled |
| **sensor_fault** | string | `under_range` while the input is in fault, otherwise `null`. While set, the level, percentage, display and volume tags are `null` |
| **zero_m** / **span_m** / **fluid_density** | number | Calibration values in effect (Operator Sensor Calibration on only) |
| **operator_calibration** | boolean | `true` while Operator Sensor Calibration is on |

#### Sensor fault

The input is checked against the configured minimum input (4 mA), NAMUR NE43 style:

| Reading | Result |
|---------|--------|
| at or above the minimum | published normally |
| up to 0.2 below it (3.8 - 4 mA) | a healthy sensor at the end of its range: read as the minimum input (empty for a Submersible; a Radar reads inverted, so full) |
| more than 0.2 below it (under 3.8 mA) | `sensor_fault` = `under_range` |

The device app enters a fault after 3 consecutive fault samples and leaves it after 3
consecutive good ones (about 1 a second), so a noisy loop cannot flicker it; a single
spike holds the last values. The processor variant decides on each message. While in
fault the level, percentage, display and volume tags are `null` (so no peer or UI
shows a stale level as live), `raw_level_reading` keeps updating, the level alarm is
not evaluated, and the app's own page shows a warning with the reading. One warning is
logged on entering a fault and one info line on recovery.

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
