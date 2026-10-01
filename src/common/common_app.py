import itertools
import logging

from .common_config import SensorType

log = logging.getLogger(__name__)

WATER_DENSITY = 1000.0  # kg/m³


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
    async def handle_update(self, result):
        log.info(f"Level sensor reading: {result}")

        if result is None or result < self.config.sensor_min_mA.value:
            return

        level = self._level_reading(result)

        await self.tags.level_filled_percentage.set(self._filled_percentage(result))
        # level_reading is canonical metres: peer apps (sia-local-control,
        # cylindrical-tank) consume it and do their own scaling. The UI gauge
        # can't scale a $tag reference in the browser, so the configured-unit
        # copy is published alongside it rather than replacing it.
        await self.tags.level_reading.set(level)
        await self.tags.level_reading_display.set(
            self.config.metres_to_depth_units(level)
        )
        await self.tags.raw_level_reading.set(result)
        # Always publish volume so peer apps (e.g. the HMI) can show it
        # regardless of how this sensor's own UI is configured. hide_volume /
        # Reading Type only affect this app's own gauge, not the data on the wire.
        await self.tags.level_volume.set(self._volume(result))

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
