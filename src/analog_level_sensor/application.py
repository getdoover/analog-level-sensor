import logging

from pydoover.docker import Application

from .app_config import AnalogLevelSensorConfig, SensorType
from .app_tags import AnalogLevelSensorTags
from .app_ui import AnalogLevelSensorUI


log = logging.getLogger()


class AnalogLevelSensorApplication(Application):
    config: AnalogLevelSensorConfig
    tags: AnalogLevelSensorTags

    config_cls = AnalogLevelSensorConfig
    tags_cls = AnalogLevelSensorTags
    ui_cls = AnalogLevelSensorUI

    async def setup(self):
        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), True)

        freq = self.config.polling_frequency.value
        if freq and freq > 0:
            self.loop_target_period = 1 / freq

    async def main_loop(self):
        result = await self.platform_iface.fetch_ai(int(self.config.ai_pin.value))
        log.info(f"Level sensor reading: {result}")

        if result is None or result < self.config.sensor_min_mA.value:
            return

        await self.tags.level_filled_percentage.set(self._filled_percentage(result))
        await self.tags.level_reading.set(self._level_reading(result))
        await self.tags.raw_level_reading.set(result)
        if not self.config.hide_volume.value:
            await self.tags.level_volume.set(self._volume(result))

    def _map_value(self, value, low_a, high_a, low_b, high_b, invert=False):
        if invert and self.config.type.value is SensorType.RADAR:
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
        return self._map_value(
            perc,
            0,
            100,
            self.config.sensor_min_m.value,
            self.config.sensor_max_m.value,
        )

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

    async def on_shutdown_at(self, _seconds: int):
        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), False)

    @staticmethod
    def _get_volume(level, volume_curve):
        if not volume_curve:
            return None

        curve = {}
        for point in volume_curve:
            curve[point.level.value] = point.volume.value

        sorted_levels = sorted(float(l) for l in curve.keys())
        sorted_keys = sorted(curve.keys())

        for i in range(len(sorted_levels) - 1):
            if sorted_levels[i] <= level <= sorted_levels[i + 1]:
                x1, y1 = sorted_levels[i], curve[sorted_keys[i]]
                x2, y2 = sorted_levels[i + 1], curve[sorted_keys[i + 1]]
                return y1 + (level - x1) * (y2 - y1) / (x2 - x1)

        if level < sorted_levels[0]:
            x1, y1 = sorted_levels[0], curve[sorted_keys[0]]
            x2, y2 = sorted_levels[1], curve[sorted_keys[1]]
        else:
            x1, y1 = sorted_levels[-2], curve[sorted_keys[-2]]
            x2, y2 = sorted_levels[-1], curve[sorted_keys[-1]]

        return y1 + (level - x1) * (y2 - y1) / (x2 - x1)

    @staticmethod
    def _get_max_volume(volume_curve):
        if not volume_curve:
            return None
        return max(point.volume.value for point in volume_curve)
