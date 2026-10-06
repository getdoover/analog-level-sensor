import itertools
import logging

from .common_config import SensorType
from .common_tags import SENSOR_FAULT_DEFAULT_MESSAGE, SENSOR_FAULT_UNDER_RANGE

log = logging.getLogger(__name__)

WATER_DENSITY = 1000.0  # kg/m³

# Loop-current check, relative to the configured minimum input (4 mA). Below
# the minimum is a healthy sensor at the end of its range (an empty tank; full
# for a Radar, which reads inverted) and reads as the minimum, as a submersible
# at its zero often sits a little under 4 mA. Only a dead loop is a fault: a
# reading this fraction of the input span below the minimum, i.e. under 1 mA on
# 4-20 mA, or under 1000 on a 4000-20000 µA raw input.
UNDER_RANGE_MARGIN = 3 / 16
# Consecutive samples (about one a second on the device) needed to enter, and
# to leave, a sensor fault, so a noisy loop cannot flicker it.
FAULT_DEBOUNCE_SAMPLES = 3


def config_fluid_density(config) -> float:
    """The configured fluid density (kg/m³). Older deployment configs predate
    the field, and a null or non-positive value is not a density: both fall
    back to water."""
    value = getattr(config, "fluid_density", None)
    density = None if value is None else value.value
    if density is None or float(density) <= 0:
        return WATER_DENSITY
    return float(density)


class CommonAnalogLevelSensorApplication:
    # Samples to enter / leave a sensor fault. The processor overrides it: each
    # of its invocations is a fresh instance, so a count would never build up.
    fault_debounce_samples = FAULT_DEBOUNCE_SAMPLES

    async def handle_update(self, result) -> float | None:
        """Publish one raw input sample.

        Returns the reading the level, percentage and volume were derived from
        (the sample, raised to the minimum input in the clamp band), or None when
        the sample published no level: no reading, a sensor fault, or an
        under-range sample that has not yet become a fault.
        """
        log.info(f"Level sensor reading: {result}")

        if result is None:
            return None

        # Always, a fault included: it is the one number that says what the
        # sensor is doing (the HMI Sensor tab and the fault warning show it).
        await self.tags.raw_level_reading.set(result)

        minimum = self.config.sensor_min_mA.value
        under_range = result < minimum - self._under_range_margin()
        if self._update_fault(result, under_range):
            await self._publish_fault()
            return None
        if under_range:
            # Not a fault yet (a spike, or the start of one): hold the values
            # already published rather than derive any from it.
            return None

        # Within the under-range margin below the minimum is a healthy sensor at the
        # end of its range, so it reads as the minimum input.
        reading = max(result, minimum)
        level = self._level_reading(reading)

        await self.tags.level_filled_percentage.set(self._filled_percentage(reading))
        # level_reading is canonical metres: peer apps (sia-local-control,
        # cylindrical-tank) consume it and do their own scaling. The UI gauge
        # can't scale a $tag reference in the browser, so the configured-unit
        # copy is published alongside it rather than replacing it.
        await self.tags.level_reading.set(level)
        await self.tags.level_reading_display.set(
            self.config.metres_to_depth_units(level)
        )
        # Always publish volume so peer apps (e.g. the HMI) can show it
        # regardless of how this sensor's own UI is configured. hide_volume /
        # Reading Type only affect this app's own gauge, not the data on the wire.
        await self.tags.level_volume.set(self._volume(reading))
        await self.tags.sensor_fault.set(None)
        await self.tags.sensor_fault_hidden.set(True)
        return reading

    # -- sensor fault (sensor_fault tag) ---------------------------------------

    def _in_sensor_fault(self) -> bool:
        in_fault = getattr(self, "_sensor_fault_active", None)
        if in_fault is None:
            # First sample: a fault published before a restart (or by the
            # previous processor invocation) still stands, so it still takes
            # good samples to clear it.
            in_fault = self.tags.sensor_fault.get() is not None
            self._sensor_fault_active = in_fault
            self._sensor_fault_streak = 0
            self._sensor_fault_input = None
        return in_fault

    def _update_fault(self, result, under_range: bool) -> bool:
        """Debounce the under-range check. True while the input is in fault."""
        in_fault = self._in_sensor_fault()
        if under_range:
            self._sensor_fault_input = result
        if under_range == in_fault:
            self._sensor_fault_streak = 0
            return in_fault

        self._sensor_fault_streak += 1
        if self._sensor_fault_streak < self.fault_debounce_samples:
            return in_fault

        self._sensor_fault_streak = 0
        self._sensor_fault_active = under_range
        units = self._input_units()
        if under_range:
            log.warning(
                "Sensor input %s %s is below range (minimum %s %s): level, "
                "percentage and volume cleared until it recovers",
                result,
                units,
                self.config.sensor_min_mA.value,
                units,
            )
        else:
            log.info("Sensor input back in range at %s %s", result, units)
        return under_range

    async def _publish_fault(self):
        # Clear the derived values rather than leave the last good ones looking
        # live to peers and UIs.
        await self.tags.level_filled_percentage.set(None)
        await self.tags.level_reading.set(None)
        await self.tags.level_reading_display.set(None)
        await self.tags.level_volume.set(None)
        await self.tags.sensor_fault.set(SENSOR_FAULT_UNDER_RANGE)
        await self.tags.sensor_fault_message.set(self._fault_message())
        await self.tags.sensor_fault_hidden.set(False)

    def _under_range_margin(self) -> float:
        """How far below the minimum input a reading still counts as the
        minimum (beyond it the loop is dead), in the configured input units."""
        span = self.config.sensor_max_mA.value - self.config.sensor_min_mA.value
        return abs(span) * UNDER_RANGE_MARGIN

    def _input_units(self) -> str:
        units = getattr(self.config, "input_units", None)
        return (units.value if units is not None else None) or "mA"

    def _fault_message(self) -> str:
        value = getattr(self, "_sensor_fault_input", None)
        if value is None:
            return SENSOR_FAULT_DEFAULT_MESSAGE
        return (
            f"Sensor signal below range ({value:.2f} {self._input_units()}) "
            "— check the sensor and its wiring"
        )

    def _map_value(self, value, low_a, high_a, low_b, high_b, invert=False):
        if invert and self.config.type.value == SensorType.RADAR:
            return (high_b - low_b) - ((value - low_a) / (high_a - low_a)) * (
                high_b - low_b
            )
        return ((value - low_a) / (high_a - low_a)) * (high_b - low_b) + low_b

    def _sensor_percentage(self, reading) -> float:
        return self._map_value(
            reading,
            self.config.sensor_min_mA.value,
            self.config.sensor_max_mA.value,
            0,
            100,
            invert=True,
        )

    def _level_reading(self, reading) -> float:
        perc = self._sensor_percentage(reading)
        zero = self._zero_m()
        level = self._map_value(perc, 0, 100, zero, self._span_m())
        if self.config.type.value == SensorType.SUBMERSIBLE:
            # Scale only the fluid column; the zero is the sensor's mounting
            # height above the tank floor (negative below it) and does not
            # depend on the fluid.
            level = zero + (level - zero) * WATER_DENSITY / self._fluid_density()
        return level

    # The three values the 4-20 mA conversion runs on. The device app overrides
    # these with the operator's calibration when Operator Sensor Calibration is
    # enabled; otherwise (and in the processor) they are the deployment config.
    def _zero_m(self) -> float:
        """The minimum level (m): at the minimum input (4 mA), or at the
        maximum input (20 mA) for a Radar, which reads inverted. May be
        negative (below the tank datum); the level then reads below 0 m."""
        return self.config.sensor_min_m.value

    def _span_m(self) -> float:
        """The maximum level (m): at the maximum input (20 mA), or at the
        minimum input (4 mA) for a Radar, which reads inverted."""
        return self.config.sensor_max_m.value

    def _fluid_density(self) -> float:
        return config_fluid_density(self.config)

    def _filled_percentage(self, reading) -> float | None:
        lev = self._level_reading(reading)

        curve = self.config.volume_curve.elements
        if len(curve) < 2:
            return self._map_value(
                lev, self.config.empty_level.value, self.config.full_level.value, 0, 100
            )

        vol = self._get_volume(lev, curve)
        max_vol = self._get_max_volume(curve)
        if vol is None or max_vol is None:
            return None
        return round(vol / max_vol * 100, 3)

    def _volume(self, reading) -> float | None:
        curve = self.config.volume_curve.elements
        if len(curve) >= 2:
            return self._get_volume(self._level_reading(reading), curve)

        perc = self._filled_percentage(reading)
        if perc is None:
            return None
        return self.config.max_volume.value * (perc / 100)

    @staticmethod
    def _get_volume(level, volume_curve):
        if not volume_curve:
            return None

        # Sort (level, volume) pairs together as floats so the mapping is
        # preserved even if config values arrive as strings.
        points = sorted(
            (float(p.level.value), float(p.volume.value)) for p in volume_curve
        )

        # Interpolate within the curve.
        for (x1, y1), (x2, y2) in itertools.pairwise(points):
            if x1 <= level <= x2:
                return y1 + (level - x1) * (y2 - y1) / (x2 - x1)

        # Outside the curve's range, extrapolate off the nearest end segment.
        # This intentionally allows volume (and filled %) to run past the
        # curve's bounds so misconfiguration shows up rather than being hidden.
        if level < points[0][0]:
            (x1, y1), (x2, y2) = points[0], points[1]
        else:
            (x1, y1), (x2, y2) = points[-2], points[-1]

        return y1 + (level - x1) * (y2 - y1) / (x2 - x1)

    @staticmethod
    def _get_max_volume(volume_curve):
        if not volume_curve:
            return None
        return max(point.volume.value for point in volume_curve)


CommonApplication = CommonAnalogLevelSensorApplication
