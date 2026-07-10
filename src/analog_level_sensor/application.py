from pydoover.docker import Application
from pydoover.models import NotificationSeverity

from common.common_app import CommonAnalogLevelSensorApplication

from .alarm import Alarm, AlarmType, evaluate
from .app_config import AlarmSource, AnalogLevelSensorDeviceConfig
from .app_tags import AnalogLevelSensorDeviceTags
from .app_ui import AnalogLevelSensorDeviceUI


class AnalogLevelSensorDeviceApplication(
    Application,
    CommonAnalogLevelSensorApplication,
):
    config: AnalogLevelSensorDeviceConfig
    tags: AnalogLevelSensorDeviceTags

    config_cls = AnalogLevelSensorDeviceConfig
    tags_cls = AnalogLevelSensorDeviceTags
    ui_cls = AnalogLevelSensorDeviceUI

    async def setup(self):
        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), True)

        freq = self.config.polling_frequency.value
        if freq and freq > 0:
            self.loop_target_period = 1 / freq

        self.alarm = Alarm(
            grace_period=self.config.alarm_grace_period,
            renotify_interval=self.config.alarm_renotify_interval,
        )

    async def main_loop(self):
        result = await self.platform_iface.fetch_ai(int(self.config.ai_pin.value))

        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), True)

        await self.handle_update(result)

    async def handle_update(self, result):
        await super().handle_update(result)

        # The shared handler drops under-range samples before writing any tag,
        # so keep the alarm blind to them too rather than alarming on a reading
        # the rest of the app has decided not to trust.
        if result is None or result < self.config.sensor_min_mA.value:
            return

        await self._check_alarm(result)

    async def on_shutdown_at(self, _seconds: int):
        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), False)

    def _alarm_value(self, result):
        """The quantity the alarm tracks, derived from the raw sensor reading."""
        source = self.config.alarm_source
        if source is AlarmSource.percentage:
            return self._filled_percentage(result)
        if source is AlarmSource.volume:
            return self._volume(result)
        return self._level_reading(result)

    @staticmethod
    def _slider_value(slider):
        """A slider the operator has never moved has no stored value, and these
        sliders have no default, so reading one raises. Treat that as unset."""
        try:
            return slider.value
        except KeyError:
            return None

    def _alarm_bounds(self):
        """Read the alarm setpoint(s) from whichever slider the mode is using.

        Returns (point, low, high). Any of them may be None when the operator
        has not moved the slider yet, which evaluate() treats as "no bound".
        """
        if self.config.alarm_type is AlarmType.allowed_range:
            value = self._slider_value(self.ui.alarm_range)
            # the dual slider reports [low, high], but not always in that order
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                return None, None, None
            low, high = sorted(value)
            return None, low, high

        return self._slider_value(self.ui.alarm_point), None, None

    async def _check_alarm(self, result):
        if not self.config.alarm_enabled:
            self.alarm.clear()
            return

        point, low, high = self._alarm_bounds()
        reading = self._alarm_value(result)
        breach = evaluate(
            reading, self.config.alarm_type, point=point, low=low, high=high
        )

        if self.alarm.update(breach):
            await self.send_notification(
                self._alarm_message(reading, breach),
                title=f"{self.app_display_name} alarm",
                severity=NotificationSeverity.Warn,
            )

    def _alarm_message(self, reading, breach):
        units = self.config.alarm_units
        suffix = f" {units}" if units else ""
        return (
            f"{self.app_display_name} has {breach.direction.value} "
            f"{breach.bound:g}{suffix} with a value of {reading:g}{suffix}"
        )


AnalogLevelSensorApplication = AnalogLevelSensorDeviceApplication
