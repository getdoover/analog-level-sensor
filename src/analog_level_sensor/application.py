from pydoover import ui
from pydoover.docker import Application
from pydoover.rpc import RPCError

from common.common_app import CommonAnalogLevelSensorApplication

from . import calibration
from .alarm import Alarm, AlarmType, evaluate
from .app_config import AlarmSource, AnalogLevelSensorDeviceConfig
from .app_notifications import AnalogLevelSensorDeviceNotifications
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
    notifications_cls = AnalogLevelSensorDeviceNotifications

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

        # Before the first reading: an offline reboot restores the operator
        # calibration from its readback tags, which a publish would overwrite.
        self.calibration = calibration.SensorCalibration(self)
        await self.calibration.startup()

    async def main_loop(self):
        await self.calibration.maintain()

        result = await self.platform_iface.fetch_ai(int(self.config.ai_pin.value))

        if self.config.power_pin.value is not None:
            await self.platform_iface.set_do(int(self.config.power_pin.value), True)

        await self.handle_update(result)

    async def handle_update(self, result):
        await super().handle_update(result)

        # The shared handler drops under-range samples before writing any tag,
        # so keep the alarm blind to them too rather than alarming on a reading
        # the rest of the app has decided not to trust.
        if result is None:
            return
        if result < self.config.sensor_min_mA.value:
            if self._calibration_active():
                # The HMI Sensor tab shows the live loop current from this tag,
                # and a sensor at or just below its zero (empty tank, broken
                # loop) is exactly when an operator sets the zero, so the tag
                # must not freeze at the last in-range reading. The level,
                # percentage, volume and alarm still skip the sample.
                await self.tags.raw_level_reading.set(result)
            return

        await self._check_alarm(result)

    # -- Operator Sensor Calibration (calibration.py) ---------------------------
    # The 4-20 mA conversion (common_app._level_reading) runs on these, so the
    # level, display, percentage, volume and alarm all follow the operator's
    # values. With the feature off they are the config, exactly as before.

    def _calibration_active(self) -> bool:
        return (
            self.config.operator_calibration_enabled
            and getattr(self, "calibration", None) is not None
        )

    def _zero_m(self):
        if self._calibration_active():
            return self.calibration.effective().zero_m
        return super()._zero_m()

    def _span_m(self):
        if self._calibration_active():
            return self.calibration.effective().span_m
        return super()._span_m()

    def _fluid_density(self) -> float:
        if self._calibration_active():
            return self.calibration.effective().fluid_density
        return super()._fluid_density()

    @ui.handler(calibration.RPC_PATTERN, auto_update=False)
    async def on_calibration_value(self, ctx, value):
        """``zero_m`` / ``span_m`` / ``fluid_density`` from the cloud input or
        the HMI. No ``parser=float``: pydoover reports a parser exception as
        INTERNAL_ERROR, so the value is parsed here and a non-numeric one gets
        INVALID like an out-of-range one. The value is stored by the request
        itself (``auto_update=False``), so a refused one leaves ui_cmds as is."""
        applied = await self._sensor_calibration().request(ctx.method, value)
        return {ctx.method: applied}

    @ui.handler(calibration.RESET_ELEMENT, auto_update=False)
    async def on_reset_calibration(self, ctx, value):
        return await self._sensor_calibration().reset()

    def _sensor_calibration(self) -> calibration.SensorCalibration:
        # ui_cmds is subscribed before setup() creates it.
        setting = getattr(self, "calibration", None)
        if setting is None:
            raise RPCError("UNAVAILABLE", "the sensor app is still starting")
        return setting

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
        # The slider bounds and the notification message are both in the
        # configured depth unit, so compare on that scale rather than in the
        # canonical metres level_reading carries.
        return self.config.metres_to_depth_units(self._level_reading(result))

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
            # No title: the server substitutes the agent's display name, which
            # is the device name and is what an operator expects to see.
            await self.notifications.level_alarm.send(
                self._alarm_message(reading, breach)
            )

    @staticmethod
    def _format_value(value):
        return f"{round(value, 2):g}"

    def _alarm_message(self, reading, breach):
        units = self.config.alarm_units
        suffix = f" {units}" if units else ""
        return (
            f"{self.app_display_name} has {breach.direction.value} "
            f"{self._format_value(breach.bound)}{suffix} with a value of "
            f"{self._format_value(reading)}{suffix}"
        )


AnalogLevelSensorApplication = AnalogLevelSensorDeviceApplication
